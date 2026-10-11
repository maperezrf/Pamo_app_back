from integrations.falabella.functions.get_package_items import get_package_items
from integrations.falabella.functions.get_shipping_document import get_shipping_document
from integrations.mercadolibre.functions.get_shipment import get_shipment
from integrations.mercadolibre.functions.get_shipment_label import get_shipment_label, is_label_available
from products.models import Marketplace

from ..models import MarketplaceLabel, MarketplaceOrder

# Canales que traen su propia guía (no se genera en Envía): Mercado Libre y
# Falabella se la piden a su API; la de Sodimac la genera el RPA y llega por
# webhook (`MarketplaceLabel`, ver `sodimac_rpa`).
CHANNEL_LABELS = (Marketplace.MERCADOLIBRE, Marketplace.FALABELLA, Marketplace.SODIMAC)


def fetch_dispatch_label(order):
    """Guía del canal para el pedido de Shopify `order`.

    Devuelve ({"pdf": bytes, "tracking_number": str}, "") o (None, motivo)
    si todavía no se puede tener: guía aún no generada por el canal, o canal
    sin guía propia (la tienda web y Madecentro la generan en Envía con el
    botón "Generar guía"). Sodimac agrega
    `relation_pdf` (la relación / remesa, que va aparte del rótulo).

    Ojo: en Mercado Libre, descargar una guía que no se ha impreso
    (`ready_to_print`) la marca como impresa.
    """
    if order.marketplace not in CHANNEL_LABELS:
        return None, "Este canal no trae guía propia: se genera en Envía (botón \"Generar guía\")."
    row = MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id").first()
    if order.marketplace == Marketplace.SODIMAC and row:
        return _uploaded_label(row)
    if order.marketplace == Marketplace.MERCADOLIBRE and row:
        return _mercadolibre_label(row)
    if order.marketplace == Marketplace.FALABELLA and row:
        return _falabella_label(row)
    return None, "Pedido de marketplace sin registro local: no se sabe de dónde pedir la guía."


def _uploaded_label(row):
    label = MarketplaceLabel.objects.filter(order=row).first()
    if label is None:
        reason = {
            MarketplaceOrder.LabelRequestStatus.REQUESTED: "Esperando la guía de Sodimac: pedida al RPA.",
            MarketplaceOrder.LabelRequestStatus.ERROR: f"Guía de Sodimac con error, se pide otra vez: {row.label_request_note}",
            MarketplaceOrder.LabelRequestStatus.MANUAL: f"Guía de Sodimac en revisión manual: {row.label_request_note}",
        }.get(row.label_request_status, "Esperando la guía de Sodimac (se pide al RPA en el próximo sync).")
        return None, reason
    return {"pdf": bytes(label.label_pdf), "tracking_number": label.tracking_number, "relation_pdf": bytes(label.relation_pdf)}, ""


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
