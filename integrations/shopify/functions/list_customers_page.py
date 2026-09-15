from ..client import ShopifyClient
from ..queries import LIST_CUSTOMERS_PAGE


def list_customers_page(cursor=None):
    """Trae una página (100) de clientes de Shopify, ya normalizados. La usa
    la reconciliación de la app `customers`.

    `cursor`: el `end_cursor` de la página anterior, o `None` para la
    primera.

    Devuelve {"customers": [...], "has_next_page": bool, "end_cursor": str|None}.
    Cada cliente normalizado: {"shopify_id", "email", "phone",
    "first_name", "last_name", "shopify_updated_at", "default_address_id",
    "identification"}. Si el cliente no tiene `defaultAddress`,
    `identification`/`default_address_id` quedan en `""`. `default_address_id`
    se guarda tal cual lo entrega Shopify, sin recortar prefijo (ver nota en
    `create_customer_address.py`).
    """
    response = ShopifyClient().request_graphql(LIST_CUSTOMERS_PAGE, variables={"cursor": cursor})
    data = (response.get("data") or {}).get("customers") or {}
    page_info = data.get("pageInfo") or {}
    edges = data.get("edges") or []
    return {
        "customers": [_normalize_customer(edge["node"]) for edge in edges],
        "has_next_page": bool(page_info.get("hasNextPage")),
        "end_cursor": page_info.get("endCursor"),
    }


def _normalize_customer(raw):
    default_address = raw.get("defaultAddress") or {}
    return {
        "shopify_id": raw["id"].removeprefix("gid://shopify/Customer/"),
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "first_name": raw.get("firstName") or "",
        "last_name": raw.get("lastName") or "",
        "shopify_updated_at": raw.get("updatedAt"),
        "default_address_id": default_address.get("id") or "",
        "identification": default_address.get("company") or "",
    }
