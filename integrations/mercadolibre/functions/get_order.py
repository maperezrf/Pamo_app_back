from ..client import MercadoLibreClient


def get_order(order_id):
    """Trae un pedido de Mercado Libre (`GET /orders/{id}`), normalizado.

    Devuelve:
        {"order_id", "pack_id", "shipment_id", "status", "created_at",
         "buyer": {"id", "nickname"},
         "items": [{"sku", "quantity", "price", "title", "item_id",
                    "variation_id"}],
         "raw"}

    Campos tomados de la documentación de Mercado Libre, **sin verificar
    todavía contra un pedido real** (levantamiento de la fase 0, ver
    docs/implementations-plans/mercadolibre-orders-import.md):
    - `sku` = `order_items[].item.seller_sku`. Si viene vacío queda `""`
      -- a propósito NO se usa el `item.id` como SKU (nunca resolvería en
      Shopify). Falta confirmar que en una variación trae el SKU de la
      variación.
    - `buyer` solo con id y apodo: qué otros datos del comprador expone el
      pedido (email, teléfono, nombre) es justamente lo que se levanta.
    - `pack_id` vacío si el pedido no es parte de un carrito.

    `raw` es el JSON crudo, solo mientras dura el levantamiento de datos;
    se retira al cerrar la fase 0.
    """
    raw = MercadoLibreClient().get(f"/orders/{order_id}")
    buyer = raw.get("buyer") or {}
    return {
        "order_id": str(raw["id"]),
        "pack_id": _text(raw.get("pack_id")),
        "shipment_id": _text((raw.get("shipping") or {}).get("id")),
        "status": _text(raw.get("status")),
        "created_at": _text(raw.get("date_created")),
        "buyer": {"id": _text(buyer.get("id")), "nickname": _text(buyer.get("nickname"))},
        "items": [_normalize_item(entry) for entry in raw.get("order_items") or []],
        "raw": raw,
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
