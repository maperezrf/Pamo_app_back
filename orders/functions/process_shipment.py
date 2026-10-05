from config.constants import FULFILLMENT_PRIORITY_LOCATION_ID
from integrations.shopify.functions.create_order import ShopifyOrderCreationError, create_order
from integrations.shopify.functions.get_variant_inventory_by_sku import get_variant_inventory_by_sku
from integrations.shopify.functions.list_orders_page import list_orders_page
from products.functions.expand_product import EmptyKitError, expand_product
from products.functions.resolve_marketplace_sku import resolve_marketplace_sku

from ..models import MarketplaceOrder
from .select_fulfillment_location import select_fulfillment_location

# Los pedidos de marketplace llegan ya pagados por el comprador -- no se
# está cobrando nada a través de Shopify, solo se registra la venta.
# Sodimac es la excepción (paga a crédito): llama con "PENDING".
FINANCIAL_STATUS = "PAID"


def process_shipment(orders, customer_id, financial_status=FINANCIAL_STATUS, requires_shipping=True):
    """Crea UNA orden de Shopify para las órdenes de marketplace de un mismo
    envío, a nombre del cliente fijo `customer_id`.

    Un envío es una guía y sale de una sola bodega (regla de negocio): los
    ítems de todas las órdenes se evalúan juntos en
    `select_fulfillment_location` y la orden de Shopify lleva todas las
    líneas. Falabella llama con `[order]` (un pedido = un envío); Mercado
    Libre con todas las órdenes de un pack. Ver
    docs/implementations-plans/mercadolibre-orders-import.md (decisión E).

    - Un SKU que es kit va como una línea por componente, con el precio
      repartido (`products.expand_product`).
    - Algún SKU no resuelve en Shopify -> todas quedan `error_creando_orden`,
      sin orden parcial.
    - Sin bodega que cubra el envío completo -> novedad, pero la orden se
      crea igual (docs/implementations-plans/shopify-inventory-by-location.md).
    - Shopify rechaza la orden -> todas `error_creando_orden`.
    - Éxito -> todas `orden_creada` con el mismo `shopify_order_id`.
    - `requires_shipping`: si las líneas de la orden requieren despacho en
      Shopify. Mercado Libre llama con `False` (decisión de negocio,
      2026-10-05); Madecentro, Sodimac y Falabella, con despacho (por
      defecto).
    - Ya existe en Shopify una orden con la etiqueta de alguno de los
      pedidos (`<marketplace>-<número>`) -> se vincula esa orden, sin crear
      otra. Cubre a otro proceso (u otra base) que ya la creó; ver
      "Idempotencia" en docs/apps/orders.md.

    El resultado se guarda en cada orden; no devuelve nada.
    """
    orders = list(orders)
    existing = _find_existing_order(orders)
    if existing:
        _mark_created(orders, customer_id, existing["id"], existing["name"])
        return

    resolved = _resolve_line_items(orders)
    if resolved is None:
        return  # ya quedaron marcadas error_creando_orden
    line_items, inventory_lines = resolved

    _assign_fulfillment_location(orders, inventory_lines)

    try:
        result = create_order(
            items=line_items,
            customer_id=customer_id,
            financial_status=financial_status,
            note=_order_note(orders),
            tags=[orders[0].marketplace, *_order_keys(orders)],
            requires_shipping=requires_shipping,
        )
    except ShopifyOrderCreationError as error:
        _mark_error(orders, str(error))
        return

    _mark_created(orders, customer_id, result["order_id"], result["order_name"])


def _order_numbers(orders):
    # Números del marketplace del envío, sin repetir (un pack los comparte).
    return list(dict.fromkeys(order.marketplace_order_number or order.marketplace_order_id for order in orders))


def _order_keys(orders):
    """Etiqueta única por pedido de marketplace (`falabella-3254084998`):
    identifica en Shopify la orden de ese pedido, sin depender de la base
    local."""
    marketplace = orders[0].marketplace
    return [f"{marketplace}-{number}" for number in _order_numbers(orders)]


def _find_existing_order(orders):
    """Orden de Shopify que ya lleva la etiqueta de alguno de los pedidos,
    o None. La búsqueda de Shopify se indexa con unos segundos de retraso:
    no reemplaza a `claim_orders` dentro de la misma base."""
    query = " OR ".join(f'tag:"{key}"' for key in _order_keys(orders))
    found = list_orders_page(first=1, query=query)["orders"]
    return found[0] if found else None


def _mark_created(orders, customer_id, shopify_order_id, shopify_order_name):
    for order in orders:
        order.status = MarketplaceOrder.Status.CREATED
        order.shopify_customer_id = customer_id
        order.shopify_order_id = shopify_order_id
        order.shopify_order_name = shopify_order_name
        order.error_description = ""
        order.save(
            update_fields=[
                "status",
                "shopify_customer_id",
                "shopify_order_id",
                "shopify_order_name",
                "error_description",
                "updated_at",
            ]
        )


def _order_note(orders):
    # En Shopify todas las órdenes muestran el mismo cliente fijo; la nota
    # es lo que le dice al equipo quién compró realmente. Las órdenes de un
    # mismo envío son del mismo comprador; en un pack comparten número.
    first = orders[0]
    note = f"{first.get_marketplace_display()} #{', #'.join(_order_numbers(orders))}"
    buyer = " ".join(part for part in (first.customer_first_name, first.customer_last_name) if part)
    if buyer:
        note += f" — {buyer}"
    if first.customer_identification:
        note += f" {first.customer_identification_type or 'CC'} {first.customer_identification}"
    return note


def _assign_fulfillment_location(orders, inventory_lines):
    # Una decisión manual no se pisa (solo llegaría acá si el envío se
    # reintenta tras un error al crear la orden).
    if any(order.fulfillment_status == MarketplaceOrder.FulfillmentStatus.RESOLVED for order in orders):
        return
    selection = select_fulfillment_location(inventory_lines, FULFILLMENT_PRIORITY_LOCATION_ID)
    for order in orders:
        order.fulfillment_status = (
            MarketplaceOrder.FulfillmentStatus.NOVEDAD
            if selection["reason"]
            else MarketplaceOrder.FulfillmentStatus.ASSIGNED
        )
        order.fulfillment_location_id = selection["location_id"]
        order.fulfillment_location_name = selection["name"]
        order.fulfillment_note = selection["reason"]
        order.save(
            update_fields=[
                "fulfillment_status",
                "fulfillment_location_id",
                "fulfillment_location_name",
                "fulfillment_note",
                "updated_at",
            ]
        )


def _resolve_line_items(orders):
    """Traduce cada SKU del marketplace con el catálogo (`products`) y lo
    consulta en Shopify SIEMPRE (aunque el variant id ya esté cacheado): la
    misma consulta trae el stock por bodega, que cambia. Un kit se expande
    en una línea por componente, con el precio repartido. Devuelve
    (line_items para create_order, líneas de inventario para elegir
    bodega), o None si algún SKU no resuelve."""
    line_items = []
    inventory_lines = []
    for order in orders:
        for item in order.items.all():
            try:
                product = resolve_marketplace_sku(order.marketplace, item.marketplace_sku)
                lines = _shopify_lines(item, product)
            except EmptyKitError as error:
                _mark_error(orders, f"SKU {item.marketplace_sku}: {error}")
                return None
            inventories = []
            for line in lines:
                inventory = get_variant_inventory_by_sku(line["sku"]) if line["sku"] else None
                if inventory is None:
                    _mark_error(orders, f"SKU no encontrado en Shopify: {_shown_sku(item, product, line)}")
                    return None
                inventories.append(inventory)
                line_items.append(
                    {"variant_id": inventory["variant_id"], "quantity": line["quantity"], "price": line["price"]}
                )
                inventory_lines.append({**inventory, "quantity": line["quantity"]})
            _cache_inventory(item, product, lines, inventories)
    return line_items, inventory_lines


def _shopify_lines(item, product):
    """Líneas de Shopify (`sku`, `quantity`, `price`) para un ítem, con el
    catálogo de `products` (docs/apps/products.md):

    - Sin equivalencia: el mismo SKU y precio (así funcionaban todos los
      canales antes del catálogo; Falabella y Mercado Libre no tienen
      equivalencias cargadas).
    - Producto simple: el SKU de Pamo, mismo precio.
    - Kit: una línea por componente, con el precio repartido en partes
      iguales por unidad de componente (`expand_product`).
    """
    if product is None:
        return [{"sku": item.marketplace_sku, "quantity": item.quantity, "price": item.unit_price}]
    if not product.is_kit:
        return [{"sku": product.sku, "quantity": item.quantity, "price": item.unit_price}]
    return [
        {"sku": line["sku"], "quantity": line["quantity"], "price": str(line.get("price", item.unit_price))}
        for line in expand_product(product, item.quantity, item.unit_price)
    ]


def _shown_sku(item, product, line):
    shown = item.marketplace_sku or "(vacío)"
    if product is not None and product.is_kit:
        return f"{shown} (componente {line['sku']} del kit {product.sku})"
    if line["sku"] and line["sku"] != item.marketplace_sku:
        shown += f" (equivalencia {line['sku']})"
    return shown


def _cache_inventory(item, product, lines, inventories):
    """Producto simple: variant id y stock por bodega del ítem. Kit: no hay
    un único variant, así que `shopify_variant_id` queda vacío y el
    snapshot guarda un elemento por componente
    (`{"sku", "quantity", "variant_id", "locations"}`)."""
    if product is not None and product.is_kit:
        item.shopify_variant_id = ""
        item.inventory_snapshot = [
            {"sku": line["sku"], "quantity": line["quantity"], "variant_id": inventory["variant_id"],
             "locations": inventory["locations"]}
            for line, inventory in zip(lines, inventories)
        ]
    else:
        item.shopify_variant_id = inventories[0]["variant_id"]
        item.inventory_snapshot = inventories[0]["locations"]
    item.save(update_fields=["shopify_variant_id", "inventory_snapshot"])


def _mark_error(orders, description):
    for order in orders:
        order.status = MarketplaceOrder.Status.ERROR_ORDER
        order.error_description = description
        order.save(update_fields=["status", "error_description", "updated_at"])
