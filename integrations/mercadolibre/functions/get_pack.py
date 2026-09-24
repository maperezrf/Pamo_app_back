from ..client import MercadoLibreClient


def get_pack(pack_id):
    """Trae un pack (compra por carrito) de Mercado Libre
    (`GET /packs/{pack_id}`), normalizado.

    Devuelve:
        {"pack_id", "order_ids": [str], "shipment_id", "status"}

    Verificado el 2026-09-24 contra un pack real de 2 órdenes: devuelve
    `orders: [{"id", "static_tags"}]`, `shipment: {"id"}` y `status`
    (`released`). Todas las órdenes de un pack comparten el envío -- un
    envío es una guía y sale de una sola bodega (ver
    docs/implementations-plans/mercadolibre-orders-import.md, decisión E).
    """
    raw = MercadoLibreClient().get(f"/packs/{pack_id}")
    return {
        "pack_id": _text(raw.get("id")),
        "order_ids": [_text(order.get("id")) for order in raw.get("orders") or [] if order.get("id")],
        "shipment_id": _text((raw.get("shipment") or {}).get("id")),
        "status": _text(raw.get("status")),
    }


def _text(value):
    return "" if value is None else str(value).strip()
