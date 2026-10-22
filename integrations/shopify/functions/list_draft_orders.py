from ..client import ShopifyClient
from ..queries import LIST_DRAFT_ORDERS


def list_draft_orders(*, first=250, query=""):
    """Cotizaciones (draft orders) más recientes primero, ya normalizadas.
    `query`: búsqueda de Shopify tal cual (texto libre: número, cliente...).
    La usa el listado de la app `quotes`.

    Cada una: {"id" (sin `gid://`), "name", "created_at" (ISO, UTC),
    "tags", "total" (string), "customer": {"first_name", "last_name",
    "company"} o None}. Un error de Shopify se propaga.
    """
    variables = {"first": first, "query": query or None}
    response = ShopifyClient().request_graphql(LIST_DRAFT_ORDERS, variables=variables)
    nodes = ((response.get("data") or {}).get("draftOrders") or {}).get("nodes") or []
    return [_normalize(node) for node in nodes]


def _normalize(node):
    customer = node.get("customer")
    return {
        "id": node["id"].rsplit("/", 1)[-1],
        "name": node.get("name") or "",
        "created_at": node.get("createdAt") or "",
        "tags": node.get("tags") or [],
        "total": ((node.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount") or "0",
        "customer": customer
        and {
            "first_name": customer.get("firstName") or "",
            "last_name": customer.get("lastName") or "",
            "company": (customer.get("defaultAddress") or {}).get("company") or "",
        },
    }
