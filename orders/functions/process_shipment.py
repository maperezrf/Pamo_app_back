from config.constants import FULFILLMENT_PRIORITY_LOCATION_ID
from integrations.shopify.functions.create_order import ShopifyOrderCreationError, create_order
from integrations.shopify.functions.get_variant_inventory_by_sku import get_variant_inventory_by_sku

from ..models import MarketplaceOrder
from .select_fulfillment_location import select_fulfillment_location

# Los pedidos de marketplace llegan ya pagados por el comprador -- no se
# está cobrando nada a través de Shopify, solo se registra la venta.
FINANCIAL_STATUS = "PAID"


def process_shipment(orders, customer_id):
    """Crea UNA orden de Shopify para las órdenes de marketplace de un mismo
    envío, a nombre del cliente fijo `customer_id`.

    Un envío es una guía y sale de una sola bodega (regla de negocio): los
    ítems de todas las órdenes se evalúan juntos en
    `select_fulfillment_location` y la orden de Shopify lleva todas las
    líneas. Falabella llama con `[order]` (un pedido = un envío); Mercado
    Libre con todas las órdenes de un pack. Ver
    docs/implementations-plans/mercadolibre-orders-import.md (decisión E).

    - Algún SKU no resuelve en Shopify -> todas quedan `error_creando_orden`,
      sin orden parcial.
    - Sin bodega que cubra el envío completo -> novedad, pero la orden se
      crea igual (docs/implementations-plans/shopify-inventory-by-location.md).
    - Shopify rechaza la orden -> todas `error_creando_orden`.
    - Éxito -> todas `orden_creada` con el mismo `shopify_order_id`.

    El resultado se guarda en cada orden; no devuelve nada.
    """
    orders = list(orders)
    resolved = _resolve_line_items(orders)
    if resolved is None:
        return  # ya quedaron marcadas error_creando_orden
    line_items, inventory_lines = resolved

    _assign_fulfillment_location(orders, inventory_lines)

    try:
        result = create_order(
            items=line_items,
            customer_id=customer_id,
            financial_status=FINANCIAL_STATUS,
            note=_order_note(orders),
            tags=[orders[0].marketplace],
        )
    except ShopifyOrderCreationError as error:
        _mark_error(orders, str(error))
        return

    for order in orders:
        order.status = MarketplaceOrder.Status.CREATED
        order.shopify_customer_id = customer_id
        order.shopify_order_id = result["order_id"]
        order.shopify_order_name = result["order_name"]
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
    numbers = list(dict.fromkeys(order.marketplace_order_number or order.marketplace_order_id for order in orders))
    note = f"{first.get_marketplace_display()} #{', #'.join(numbers)}"
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
    """Consulta cada SKU en Shopify SIEMPRE (aunque el variant id ya esté
    cacheado): la misma consulta trae el stock por bodega, que cambia.
    Devuelve (line_items para create_order, líneas de inventario para
    elegir bodega), o None si algún SKU no resuelve."""
    line_items = []
    inventory_lines = []
    for order in orders:
        for item in order.items.all():
            inventory = get_variant_inventory_by_sku(item.marketplace_sku) if item.marketplace_sku else None
            if inventory is None:
                _mark_error(orders, f"SKU no encontrado en Shopify: {item.marketplace_sku or '(vacío)'}")
                return None
            item.shopify_variant_id = inventory["variant_id"]
            item.inventory_snapshot = inventory["locations"]
            item.save(update_fields=["shopify_variant_id", "inventory_snapshot"])
            line_items.append(
                {"variant_id": inventory["variant_id"], "quantity": item.quantity, "price": item.unit_price}
            )
            inventory_lines.append({**inventory, "quantity": item.quantity})
    return line_items, inventory_lines


def _mark_error(orders, description):
    for order in orders:
        order.status = MarketplaceOrder.Status.ERROR_ORDER
        order.error_description = description
        order.save(update_fields=["status", "error_description", "updated_at"])
