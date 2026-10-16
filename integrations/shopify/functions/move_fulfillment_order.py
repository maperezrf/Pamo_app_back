from ..client import ShopifyClient
from ..queries import MOVE_FULFILLMENT_ORDER
from .create_fulfillment import ShopifyFulfillmentError


def move_fulfillment_order(fulfillment_order_id, location_id):
    """Mueve un fulfillment order completo (`gid`) a la bodega `location_id`
    (sin prefijo). Devuelve el `gid` del fulfillment order movido (puede ser
    otro). Lanza `ShopifyFulfillmentError` si Shopify lo rechaza (p. ej. el
    producto no está en esa bodega)."""
    response = ShopifyClient().request_graphql(
        MOVE_FULFILLMENT_ORDER,
        variables={"id": fulfillment_order_id, "newLocationId": f"gid://shopify/Location/{location_id}"},
    )
    payload = (response.get("data") or {}).get("fulfillmentOrderMove") or {}
    moved = payload.get("movedFulfillmentOrder")
    if payload.get("userErrors") or not moved:
        raise ShopifyFulfillmentError(payload.get("userErrors") or [{"message": "Shopify no devolvió el movido."}])
    return moved["id"]
