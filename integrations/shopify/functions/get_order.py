from ..client import ShopifyClient
from ..queries import GET_ORDER
from .normalize_order import normalize_order

# Tope de páginas de líneas (100 cada una): un pedido real no llega cerca.
MAX_LINE_PAGES = 20


def get_order(order_id):
    """Lee un pedido de Shopify por id (sin prefijo `gid://`) con **todas**
    sus líneas. Devuelve el pedido normalizado (forma de `normalize_order`,
    `line_items_truncated=False`) o `None` si Shopify no lo encuentra
    (no existe o fue borrado).

    La usan el webhook de pedidos y la sincronización cuando un pedido de
    `list_orders_page` viene truncado. Un error de Shopify se propaga.
    """
    client = ShopifyClient()
    variables = {"id": f"gid://shopify/Order/{order_id}", "linesAfter": None}
    first_page = None
    line_nodes = []
    for _ in range(MAX_LINE_PAGES):
        response = client.request_graphql(GET_ORDER, variables=variables)
        order = (response.get("data") or {}).get("order")
        if order is None:
            return None
        first_page = first_page or order
        line_items = order.get("lineItems") or {}
        line_nodes.extend(line_items.get("nodes") or [])
        page_info = line_items.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        variables = {**variables, "linesAfter": page_info.get("endCursor")}
    return normalize_order(first_page, line_nodes)
