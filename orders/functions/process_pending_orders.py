from config.constants import FALABELLA_SHOPIFY_CUSTOMER_ID, FULFILLMENT_PRIORITY_LOCATION_ID
from integrations.shopify.functions.create_order import ShopifyOrderCreationError, create_order
from integrations.shopify.functions.get_variant_inventory_by_sku import get_variant_inventory_by_sku

from ..models import MarketplaceOrder
from .select_fulfillment_location import select_fulfillment_location

# Los pedidos de marketplace llegan ya pagados por el comprador -- no se
# está cobrando nada a través de Shopify, solo se registra la venta.
FINANCIAL_STATUS = "PAID"


def process_pending_orders(progress_callback=None, cancellation_token=None, limit=None):
    """Por cada MarketplaceOrder sin `shopify_order_id` (nuevo o de un
    error anterior -- mismo criterio, sin distinción, ver
    docs/implementations-plans/marketplace-orders-import.md), resuelve los
    SKUs y crea la orden en Shopify a nombre del cliente fijo
    (`FALABELLA_SHOPIFY_CUSTOMER_ID`). Los datos reales del comprador
    quedan en el MarketplaceOrder y en el `note` de la orden -- ver
    docs/implementations-plans/falabella-fixed-customer.md.

    Antes de crear la orden elige la bodega de despacho del pedido completo
    (`select_fulfillment_location`) con el inventario por bodega que trae
    la misma consulta del SKU. Sin bodega que cubra el pedido queda como
    novedad, pero la orden se crea igual -- ver
    docs/implementations-plans/shopify-inventory-by-location.md.

    Un pedido que falla queda marcado con su estado de error y no detiene
    el resto del lote. Sin cliente fijo configurado, falla antes de tocar
    cualquier pedido.

    `limit`: si se da, procesa como máximo esa cantidad de pedidos
    pendientes (los más antiguos primero) -- pensado para una primera
    corrida controlada contra la cuenta real (ej. `limit=1`), no para uso
    normal. `None` procesa todos los pendientes.
    """
    if not FALABELLA_SHOPIFY_CUSTOMER_ID:
        raise ValueError("FALABELLA_SHOPIFY_CUSTOMER_ID no está configurado")

    progress_callback = progress_callback or (lambda percent, step=None: None)
    pending_queryset = MarketplaceOrder.objects.filter(shopify_order_id="").order_by("created_at").prefetch_related(
        "items"
    )
    if limit:
        pending_queryset = pending_queryset[:limit]
    pending = list(pending_queryset)
    progress_callback(5, f"{len(pending)} pedidos pendientes")

    total = len(pending) or 1
    for index, order in enumerate(pending):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        _process_one_order(order, FALABELLA_SHOPIFY_CUSTOMER_ID)
        progress_callback(
            5 + int(90 * (index + 1) / total),
            f"Pedido {order.marketplace_order_number or order.marketplace_order_id}",
        )

    progress_callback(100, f"{len(pending)} pedidos pendientes procesados")


def _process_one_order(order, customer_id):
    resolved = _resolve_line_items(order)
    if resolved is None:
        return  # ya quedó marcado error_creando_orden
    line_items, inventory_lines = resolved

    _assign_fulfillment_location(order, inventory_lines)

    try:
        result = create_order(
            items=line_items,
            customer_id=customer_id,
            financial_status=FINANCIAL_STATUS,
            note=_order_note(order),
            tags=[order.marketplace],
        )
    except ShopifyOrderCreationError as error:
        order.status = MarketplaceOrder.Status.ERROR_ORDER
        order.error_description = str(error)
        order.save(update_fields=["status", "error_description", "updated_at"])
        return

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


def _order_note(order):
    # En Shopify todas las órdenes muestran el mismo cliente fijo; la nota
    # es lo que le dice al equipo quién compró realmente.
    buyer = " ".join(part for part in (order.customer_first_name, order.customer_last_name) if part)
    note = f"{order.get_marketplace_display()} #{order.marketplace_order_number}"
    if buyer:
        note += f" — {buyer}"
    if order.customer_identification:
        note += f" CC {order.customer_identification}"
    return note


def _assign_fulfillment_location(order, inventory_lines):
    # Una decisión manual no se pisa (solo llegaría acá si el pedido se
    # reintenta tras un error al crear la orden).
    if order.fulfillment_status == MarketplaceOrder.FulfillmentStatus.RESOLVED:
        return
    selection = select_fulfillment_location(inventory_lines, FULFILLMENT_PRIORITY_LOCATION_ID)
    order.fulfillment_status = (
        MarketplaceOrder.FulfillmentStatus.NOVEDAD if selection["reason"] else MarketplaceOrder.FulfillmentStatus.ASSIGNED
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


def _resolve_line_items(order):
    """Consulta cada SKU en Shopify SIEMPRE (aunque el variant id ya esté
    cacheado): la misma consulta trae el stock por bodega, que cambia.
    Devuelve (line_items para create_order, líneas de inventario para
    elegir bodega), o None si algún SKU no resuelve."""
    line_items = []
    inventory_lines = []
    for item in order.items.all():
        inventory = get_variant_inventory_by_sku(item.marketplace_sku)
        if inventory is None:
            order.status = MarketplaceOrder.Status.ERROR_ORDER
            order.error_description = f"SKU no encontrado en Shopify: {item.marketplace_sku}"
            order.save(update_fields=["status", "error_description", "updated_at"])
            return None
        item.shopify_variant_id = inventory["variant_id"]
        item.inventory_snapshot = inventory["locations"]
        item.save(update_fields=["shopify_variant_id", "inventory_snapshot"])
        line_items.append(
            {"variant_id": inventory["variant_id"], "quantity": item.quantity, "price": item.unit_price}
        )
        inventory_lines.append({**inventory, "quantity": item.quantity})
    return line_items, inventory_lines
