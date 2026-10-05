from ..client import ShopifyClient
from ..queries import LIST_ORDERS_PAGE
from .normalize_order import normalize_order

# Tope de líneas por pedido en `LIST_ORDERS_PAGE` (`lineItems(first: 30)`).
# Un pedido con más líneas se marca con `line_items_truncated`; para tenerlas
# todas, leerlo con `get_order`.
MAX_LINE_ITEMS = 30


def list_orders_page(*, first, after=None, query="", sort_key="CREATED_AT", reverse=True):
    """Trae una página de pedidos de Shopify, ya normalizados. La usa la
    sincronización de la app `orders`.

    `first`: tamaño de página. `after`: el `end_cursor` de la página
    anterior, o `None`. `query`: búsqueda de Shopify (`created_at:`,
    `updated_at:`...) ya armada por quien llama. `sort_key`: un valor de
    `OrderSortKeys` (`CREATED_AT`, `UPDATED_AT`...); `reverse=True` es más
    reciente primero.

    Devuelve {"orders": [...], "has_next_page": bool, "end_cursor": str|None}.
    Cada pedido tiene la forma de `normalize_order`. No conoce marketplaces
    ni la base de datos. Un error de Shopify (`ShopifyGraphQLError` o HTTP)
    se propaga.
    """
    variables = {"first": first, "after": after, "query": query or None, "sortKey": sort_key, "reverse": reverse}
    response = ShopifyClient().request_graphql(LIST_ORDERS_PAGE, variables=variables)
    data = (response.get("data") or {}).get("orders") or {}
    page_info = data.get("pageInfo") or {}
    return {
        "orders": [_normalize(node) for node in data.get("nodes") or []],
        "has_next_page": bool(page_info.get("hasNextPage")),
        "end_cursor": page_info.get("endCursor"),
    }


def _normalize(node):
    line_items = node.get("lineItems") or {}
    truncated = bool((line_items.get("pageInfo") or {}).get("hasNextPage"))
    return normalize_order(node, line_items.get("nodes") or [], truncated)
