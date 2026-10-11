from ..models import MarketplaceLabel

FILES = {"rotulo": "label_pdf", "relacion": "relation_pdf"}


class ChannelLabelFileNotFound(Exception):
    pass


def channel_label_file(order, kind):
    """PDF de la guía de un tercero (hoy el RPA de Sodimac) del pedido de
    Shopify `order`: `kind` es `rotulo` (la guía) o `relacion`. Devuelve
    (nombre de archivo, bytes)."""
    field = FILES.get(kind)
    if field is None:
        raise ChannelLabelFileNotFound("Documento desconocido: va rotulo o relacion.")
    label = (
        MarketplaceLabel.objects.filter(order__shopify_order_id=order.shopify_id)
        .select_related("order")
        .order_by("order_id")
        .first()
    )
    if label is None:
        raise ChannelLabelFileNotFound("El pedido no tiene guía cargada.")
    number = label.order.marketplace_order_number or label.order.marketplace_order_id
    return f"{label.order.marketplace}-{number}-{kind}.pdf", bytes(getattr(label, field))
