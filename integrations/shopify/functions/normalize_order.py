"""Normalización única de un pedido de Shopify (fragmento `OrderFields` de
`queries.py`), compartida por `list_orders_page` y `get_order` para que los
dos devuelvan exactamente la misma forma."""


def normalize_order(raw, line_nodes, line_items_truncated=False):
    """Pedido normalizado: {"id", "name", "created_at", "updated_at",
    "cancelled_at", "financial_status", "fulfillment_status", "tags",
    "email", "phone", "total", "currency", "customer", "line_items",
    "line_items_truncated"}.

    `customer` es `None` si el pedido no tiene cliente; si no, {"id",
    "first_name", "last_name", "identification", "city", "region",
    "address"} (la cédula vive en `defaultAddress.company`). Cada línea:
    {"sku", "name", "quantity", "unit_price"}. Importes y fechas como
    strings tal cual los entrega Shopify; `cancelled_at` es `None` si no está
    cancelado. Ids sin prefijo `gid://`, vacíos como `""`.
    """
    total = (raw.get("totalPriceSet") or {}).get("shopMoney") or {}
    return {
        "id": raw["id"].removeprefix("gid://shopify/Order/"),
        "name": raw.get("name") or "",
        "created_at": raw.get("createdAt") or "",
        "updated_at": raw.get("updatedAt") or "",
        "cancelled_at": raw.get("cancelledAt"),
        "financial_status": raw.get("displayFinancialStatus") or "",
        "fulfillment_status": raw.get("displayFulfillmentStatus") or "",
        "tags": raw.get("tags") or [],
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "total": total.get("amount") or "",
        "currency": total.get("currencyCode") or "",
        "customer": _normalize_customer(raw.get("customer")),
        "line_items": [_normalize_line_item(node) for node in line_nodes],
        "line_items_truncated": line_items_truncated,
    }


def _normalize_customer(raw):
    if not raw:
        return None
    address = raw.get("defaultAddress") or {}
    return {
        "id": raw["id"].removeprefix("gid://shopify/Customer/"),
        "first_name": raw.get("firstName") or "",
        "last_name": raw.get("lastName") or "",
        "identification": address.get("company") or "",
        "city": address.get("city") or "",
        "region": address.get("province") or "",
        "address": address.get("address1") or "",
    }


def _normalize_line_item(raw):
    price = (raw.get("originalUnitPriceSet") or {}).get("shopMoney") or {}
    return {
        "sku": raw.get("sku") or "",
        "name": raw.get("name") or "",
        "quantity": int(raw.get("quantity") or 0),
        "unit_price": price.get("amount") or "",
    }
