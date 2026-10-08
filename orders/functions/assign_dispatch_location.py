from config.constants import FULFILLMENT_PRIORITY_LOCATION_ID
from integrations.shopify.functions.get_variant_inventory_by_sku import get_variant_inventory_by_sku
from products.models import Marketplace

from ..models import DispatchLocation, MarketplaceOrder
from .select_fulfillment_location import select_fulfillment_location
from .shopify_order_marketplace import SHOPIFY_CHANNEL

# Canales cuyo despacho tiene otro flujo de guía, todavía sin definir
# (docs/implementations-plans/order-dispatch-to-warehouses.md, sección 4).
MANUAL_CHANNELS = {Marketplace.MADECENTRO: "Madecentro", Marketplace.SODIMAC: "Sodimac"}
ASSIGNED_STATUSES = (MarketplaceOrder.FulfillmentStatus.ASSIGNED, MarketplaceOrder.FulfillmentStatus.RESOLVED)


def assign_dispatch_location(order):
    """Bodega que despacha el pedido de Shopify `order`.

    - Marketplace: la que ya eligió la importación del pedido
      (`MarketplaceOrder.fulfillment_location_*`), incluida una resuelta a
      mano. No se recalcula.
    - Tienda web: la misma regla (`select_fulfillment_location`) sobre las
      líneas del pedido, con el inventario de Shopify en vivo.

    Devuelve (DispatchLocation o None, motivo). Sin bodega, el motivo dice
    por qué y el despacho queda para gestión manual: novedad de inventario,
    canal con otro flujo, pedido sin registro local o bodega sin registrar.
    """
    if order.marketplace in MANUAL_CHANNELS:
        return None, f"{MANUAL_CHANNELS[order.marketplace]}: su flujo de guía todavía no está definido."

    if order.marketplace and order.marketplace != SHOPIFY_CHANNEL:
        row = MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id").first()
        if row is None:
            return None, "Pedido de marketplace sin registro local (no lo creó este sistema)."
        if row.fulfillment_status not in ASSIGNED_STATUSES or not row.fulfillment_location_id:
            return None, f"Novedad de bodega: {row.fulfillment_note or 'sin bodega asignada'}."
        location_id = row.fulfillment_location_id
    else:
        location_id, reason = _web_location(order)
        if reason:
            return None, reason

    location = DispatchLocation.objects.filter(shopify_location_id=location_id).first()
    if location is None:
        return None, f"La bodega {location_id} no está en el registro: sincronizar bodegas."
    return location, ""


def _web_location(order):
    lines = []
    for line in order.lines.all():
        if not line.sku:
            return "", f"La línea \"{line.name}\" no tiene SKU."
        inventory = get_variant_inventory_by_sku(line.sku)
        if inventory is None:
            return "", f"SKU no encontrado en Shopify: {line.sku}."
        lines.append({**inventory, "quantity": line.quantity})
    if not lines:
        return "", "El pedido no tiene líneas."
    selection = select_fulfillment_location(lines, FULFILLMENT_PRIORITY_LOCATION_ID)
    if selection["reason"]:
        return "", f"Novedad de bodega: {selection['reason']}"
    return selection["location_id"], ""
