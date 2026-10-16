from django.utils import timezone

from integrations.shopify.functions.create_fulfillment import create_fulfillment
from integrations.shopify.functions.get_fulfillment_orders import get_fulfillment_orders
from integrations.shopify.functions.move_fulfillment_order import move_fulfillment_order
from products.models import Marketplace

from ..models import Dispatch, MarketplaceLabel

# Transportadora que se muestra en Shopify, por canal. La de una guía
# generada en Envía es la que eligió quien la generó (`label_carrier`).
CHANNEL_CARRIERS = {
    Marketplace.MERCADOLIBRE: "Mercado Envíos",
    Marketplace.FALABELLA: "Falabella",
    Marketplace.SODIMAC: "TCC",
}
SODIMAC_HOME_DELIVERY = "Sodimac Home Delivery"
CLOSED_STATUSES = ("CLOSED", "CANCELLED", "INCOMPLETE")


def fulfill_in_shopify(dispatch):
    """Deja la guía del despacho en Shopify: cumple el pedido desde la
    bodega del despacho con número y transportadora, sin avisar al cliente.
    Se llama después de avisar a la bodega (el proceso automático salta los
    pedidos ya cumplidos en Shopify; cumplir antes dejaría a la bodega sin
    aviso). Ver docs/apps/orders.md ("Despacho a bodegas").

    - Sin guía en el despacho, o ya cumplido por aquí: no hace nada.
    - Un solo fulfillment order abierto: si está en otra bodega lo mueve a la
      del despacho y lo cumple.
    - Ninguno abierto pero ya cumplido con esta guía (p. ej. a mano): lo
      marca como hecho.
    - Cualquier otro caso (varias bodegas, cumplido con otra guía, Shopify
      lo rechaza) queda en `shopify_fulfillment_error` y no rompe el aviso.

    Devuelve True si la guía quedó en Shopify.
    """
    if not dispatch.tracking_number or dispatch.shopify_fulfilled_at:
        return bool(dispatch.shopify_fulfilled_at)
    try:
        _fulfill(dispatch)
    except Exception as error:  # noqa: BLE001 -- queda en el despacho para revisarlo
        dispatch.shopify_fulfillment_error = f"{type(error).__name__}: {error}"[:2000]
        dispatch.save(update_fields=["shopify_fulfillment_error", "updated_at"])
        return False
    dispatch.shopify_fulfilled_at = timezone.now()
    dispatch.shopify_fulfillment_error = ""
    dispatch.save(update_fields=["shopify_fulfilled_at", "shopify_fulfillment_error", "updated_at"])
    return True


class FulfillmentNotPossible(Exception):
    """El pedido en Shopify no está como para cumplirlo solo; el texto dice
    por qué."""


def _fulfill(dispatch):
    tracking = dispatch.tracking_number
    fulfillment_orders = get_fulfillment_orders(dispatch.shopify_order.shopify_id)
    open_orders = [item for item in fulfillment_orders if item["status"] not in CLOSED_STATUSES]
    if not open_orders:
        if any(tracking in item["tracking_numbers"] for item in fulfillment_orders):
            return  # ya cumplido con esta guía (a mano o en un intento anterior)
        numbers = sorted({number for item in fulfillment_orders for number in item["tracking_numbers"]})
        raise FulfillmentNotPossible(
            f"El pedido ya no tiene nada por cumplir en Shopify (guías: {', '.join(numbers) or 'ninguna'})."
        )
    if len(open_orders) > 1:
        names = ", ".join(item["location_name"] for item in open_orders)
        raise FulfillmentNotPossible(f"El pedido está repartido en varias bodegas en Shopify ({names}): cumplir a mano.")

    fulfillment_order = open_orders[0]
    fulfillment_order_id = fulfillment_order["id"]
    target_location = dispatch.location.shopify_location_id
    if fulfillment_order["location_id"] != target_location:
        fulfillment_order_id = move_fulfillment_order(fulfillment_order_id, target_location)
    create_fulfillment(fulfillment_order_id, tracking_number=tracking, tracking_company=tracking_company(dispatch))


def tracking_company(dispatch):
    """Transportadora de la guía del despacho, como se muestra en Shopify."""
    if dispatch.label_source == Dispatch.LabelSource.ENVIA:
        return dispatch.label_carrier
    if dispatch.channel == Marketplace.SODIMAC:
        label = MarketplaceLabel.objects.filter(order__shopify_order_id=dispatch.shopify_order.shopify_id).first()
        if label is not None and not label.relation_pdf:
            return SODIMAC_HOME_DELIVERY  # guía Home Delivery: un solo PDF, sin relación de TCC
    return CHANNEL_CARRIERS.get(dispatch.channel, "")
