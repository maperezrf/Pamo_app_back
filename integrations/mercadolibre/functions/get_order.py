from ..client import MercadoLibreClient


def get_order(order_id):
    """Trae un pedido de Mercado Libre (`GET /orders/{id}`), normalizado.

    Devuelve:
        {"order_id", "pack_id", "shipment_id", "billing_info_id", "status",
         "created_at", "buyer": {"id", "nickname", "first_name",
         "last_name"},
         "items": [{"sku", "quantity", "price", "title", "item_id",
                    "variation_id"}]}

    Verificado contra 8 pedidos reales el 2026-09-24 (ver
    docs/implementations-plans/mercadolibre-orders-import.md):
    - `sku` = `order_items[].item.seller_sku`, presente siempre, también
      en variantes (`variation_id` venía vacío aunque había
      `variation_attributes`). Si viene vacío queda `""` -- a propósito NO
      se usa el `item.id` como SKU (nunca resolvería en Shopify).
    - El pedido no trae email ni teléfono del comprador; `last_name` puede
      venir vacío.
    - `billing_info_id` (`buyer.billing_info.id`) es la llave de
      `get_billing_info`.
    - `pack_id` vacío si el pedido no pasó por el carrito. Tenerlo no
      implica varias órdenes: casi todos los packs tienen una sola (ver
      `get_pack`).
    """
    raw = MercadoLibreClient().get(f"/orders/{order_id}")
    buyer = raw.get("buyer") or {}
    return {
        "order_id": str(raw["id"]),
        "pack_id": _text(raw.get("pack_id")),
        "shipment_id": _text((raw.get("shipping") or {}).get("id")),
        "billing_info_id": _text((buyer.get("billing_info") or {}).get("id")),
        "status": _text(raw.get("status")),
        "created_at": _text(raw.get("date_created")),
        "buyer": {
            "id": _text(buyer.get("id")),
            "nickname": _text(buyer.get("nickname")),
            "first_name": _text(buyer.get("first_name")),
            "last_name": _text(buyer.get("last_name")),
        },
        "items": [_normalize_item(entry) for entry in raw.get("order_items") or []],
    }


def _normalize_item(entry):
    item = entry.get("item") or {}
    return {
        "sku": _text(item.get("seller_sku")),
        "quantity": int(entry.get("quantity") or 0),
        "price": _text(entry.get("unit_price")),
        "title": _text(item.get("title")),
        "item_id": _text(item.get("id")),
        "variation_id": _text(item.get("variation_id")),
    }


def _text(value):
    return "" if value is None else str(value).strip()
