"""Normalización única de un pedido de Shopify (fragmento `OrderFields` de
`queries.py`), compartida por `list_orders_page` y `get_order` para que los
dos devuelvan exactamente la misma forma."""


def normalize_order(raw, line_nodes, line_items_truncated=False):
    """Pedido normalizado: {"id", "name", "created_at", "updated_at",
    "cancelled_at", "financial_status", "fulfillment_status", "tags",
    "email", "phone", "total", "currency", "customer", "line_items",
    "line_items_truncated", "fulfillment_orders"}.

    Cada fulfillment order: {"id", "status", "location_id",
    "location_name", "items": [{"sku", "name", "quantity"}], "tracking":
    {"number", "company", "url"}} (ver `_normalize_fulfillment_orders`).

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
        "fulfillment_orders": _normalize_fulfillment_orders(raw.get("fulfillmentOrders")),
    }


def _normalize_fulfillment_orders(raw):
    """Bodegas desde donde sale el pedido, según Shopify. Se descartan los
    `CANCELLED`, los que no tienen productos (un movimiento de bodega deja
    uno `CLOSED` vacío, visto en el pedido 20463 el 2026-10-15) y los
    `CLOSED` sin ninguna unidad cumplida (`remainingQuantity` igual a
    `totalQuantity`): es la bodega anterior cuando se cancela el
    cumplimiento y se cambia la bodega (pedidos 20381 y 20522, verificado en
    vivo el 2026-10-19). Un `CLOSED` con unidades cumplidas sí despachó y se
    conserva. `tracking` es la guía del último fulfillment de ese
    fulfillment order que tenga número."""
    result = []
    for node in (raw or {}).get("nodes") or []:
        line_nodes = (node.get("lineItems") or {}).get("nodes") or []
        if node.get("status") == "CLOSED" and not any(_fulfilled_quantity(item) for item in line_nodes):
            continue
        items = [
            {
                "sku": (item.get("lineItem") or {}).get("sku") or "",
                "name": (item.get("lineItem") or {}).get("name") or "",
                "quantity": int(item.get("totalQuantity") or 0),
            }
            for item in line_nodes
        ]
        if node.get("status") == "CANCELLED" or not any(item["quantity"] for item in items):
            continue
        assigned = node.get("assignedLocation") or {}
        result.append({
            "id": node["id"].removeprefix("gid://shopify/FulfillmentOrder/"),
            "status": node.get("status") or "",
            "location_id": ((assigned.get("location") or {}).get("id") or "").removeprefix("gid://shopify/Location/"),
            "location_name": assigned.get("name") or "",
            "items": items,
            "tracking": _last_tracking((node.get("fulfillments") or {}).get("nodes") or []),
        })
    return result


def _fulfilled_quantity(item):
    total = int(item.get("totalQuantity") or 0)
    remaining = item.get("remainingQuantity")
    return total if remaining is None else total - int(remaining)


def _last_tracking(fulfillments):
    for fulfillment in reversed(fulfillments):
        for info in fulfillment.get("trackingInfo") or []:
            if info.get("number"):
                return {"number": info["number"], "company": info.get("company") or "", "url": info.get("url") or ""}
    return {"number": "", "company": "", "url": ""}


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
