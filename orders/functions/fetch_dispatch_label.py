from config.constants import DISPATCH_FETCH_LABELS_ENABLED
from integrations.falabella.functions.get_package_items import get_package_items
from integrations.falabella.functions.get_shipping_document import get_shipping_document
from integrations.mercadolibre.functions.get_shipment import get_shipment
from integrations.mercadolibre.functions.get_shipment_label import get_shipment_label, is_label_available
from products.models import Marketplace

from ..models import MarketplaceOrder


def fetch_dispatch_label(order):
    """Guía del canal para el pedido de Shopify `order`.

    Devuelve ({"pdf": bytes, "tracking_number": str}, "") o (None, motivo)
    si todavía no se puede tener: interruptor apagado, guía aún no generada
    por el canal, o canal sin fuente de guía (tienda web: decisión abierta).

    Ojo: en Mercado Libre, descargar una guía que no se ha impreso
    (`ready_to_print`) la marca como impresa. Por eso va detrás de
    `DISPATCH_FETCH_LABELS_ENABLED`.
    """
    if not DISPATCH_FETCH_LABELS_ENABLED:
        return None, "Descarga de guías apagada (DISPATCH_FETCH_LABELS_ENABLED)."

    row = MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id").first()
    if order.marketplace == Marketplace.MERCADOLIBRE and row:
        return _mercadolibre_label(row)
    if order.marketplace == Marketplace.FALABELLA and row:
        return _falabella_label(row)
    return None, "Este canal no trae guía; falta definir de dónde sale (pedidos de la tienda web)."


def _mercadolibre_label(row):
    if not row.shipment_id:
        return None, "El pedido de Mercado Libre no tiene envío."
    shipment = get_shipment(row.shipment_id)
    if not is_label_available(shipment):
        return None, f"Guía de Mercado Libre aún no disponible ({shipment['status']}/{shipment['substatus']})."
    pdf = get_shipment_label(row.shipment_id)
    return {"pdf": pdf, "tracking_number": shipment["tracking_number"] or row.shipment_id}, ""


def _falabella_label(row):
    items = [item for item in get_package_items(row.marketplace_order_id) if item["package_id"]]
    if not items:
        return None, "El pedido de Falabella todavía no tiene paquete (sin guía)."
    pdf = get_shipping_document([item["order_item_id"] for item in items])
    return {"pdf": pdf, "tracking_number": items[0]["tracking_code"] or items[0]["package_id"]}, ""
