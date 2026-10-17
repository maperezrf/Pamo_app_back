from ..client import ShopifyClient
from ..queries import GET_FULFILLMENT_ORDER_ORDER


def get_fulfillment_order_order_id(fulfillment_order_id):
    """Id del pedido (sin `gid://`) al que pertenece un fulfillment order
    (id sin `gid://`), en vivo. Devuelve `""` si Shopify no lo encuentra.
    Un error de Shopify se propaga.
    """
    response = ShopifyClient().request_graphql(
        GET_FULFILLMENT_ORDER_ORDER,
        variables={"id": f"gid://shopify/FulfillmentOrder/{fulfillment_order_id}"},
    )
    fulfillment_order = (response.get("data") or {}).get("fulfillmentOrder") or {}
    order_id = (fulfillment_order.get("order") or {}).get("id") or ""
    return order_id.removeprefix("gid://shopify/Order/")
