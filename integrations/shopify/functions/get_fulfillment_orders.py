from ..client import ShopifyClient
from ..queries import GET_ORDER_FULFILLMENT_ORDERS


def get_fulfillment_orders(order_id):
    """Fulfillment orders de un pedido de Shopify (id sin `gid://`), en vivo.

    Devuelve [{"id": str (gid completo, lo piden las mutaciones),
    "status": str, "location_id": str (sin prefijo), "location_name": str,
    "actions": set[str], "tracking_numbers": [str]}]; lista vacía si el
    pedido no existe. Incluye los cerrados y cancelados: qué hacer con cada
    uno lo decide quien llama.
    """
    response = ShopifyClient().request_graphql(
        GET_ORDER_FULFILLMENT_ORDERS, variables={"id": f"gid://shopify/Order/{order_id}"}
    )
    order = (response.get("data") or {}).get("order") or {}
    nodes = (order.get("fulfillmentOrders") or {}).get("nodes") or []
    result = []
    for node in nodes:
        assigned = node.get("assignedLocation") or {}
        fulfillments = (node.get("fulfillments") or {}).get("nodes") or []
        result.append({
            "id": node["id"],
            "status": node.get("status") or "",
            "location_id": ((assigned.get("location") or {}).get("id") or "").removeprefix("gid://shopify/Location/"),
            "location_name": assigned.get("name") or "",
            "actions": {item["action"] for item in node.get("supportedActions") or []},
            "tracking_numbers": [
                info["number"]
                for fulfillment in fulfillments
                for info in fulfillment.get("trackingInfo") or []
                if info.get("number")
            ],
        })
    return result
