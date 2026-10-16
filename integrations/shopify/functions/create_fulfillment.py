from ..client import ShopifyClient
from ..queries import CREATE_FULFILLMENT


class ShopifyFulfillmentError(Exception):
    """Shopify rechazó mover o cumplir un fulfillment order (`userErrors`).
    El texto es el de Shopify."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(str(error.get("message", error)) for error in errors))


def create_fulfillment(fulfillment_order_id, *, tracking_number, tracking_company):
    """Cumple todas las líneas pendientes de un fulfillment order (`gid`
    completo) con la guía. Nunca avisa al cliente (`notifyCustomer: false`):
    en marketplace el cliente de Shopify es el fijo del canal (a mano,
    Shopify le mandó el correo de envío a Sodimac el 2026-10-15).

    Devuelve el id del fulfillment (sin prefijo). Lanza
    `ShopifyFulfillmentError` si Shopify lo rechaza.
    """
    variables = {
        "fulfillment": {
            "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fulfillment_order_id}],
            "trackingInfo": {"number": tracking_number, "company": tracking_company},
            "notifyCustomer": False,
        }
    }
    response = ShopifyClient().request_graphql(CREATE_FULFILLMENT, variables=variables)
    payload = (response.get("data") or {}).get("fulfillmentCreate") or {}
    fulfillment = payload.get("fulfillment")
    if payload.get("userErrors") or not fulfillment:
        raise ShopifyFulfillmentError(payload.get("userErrors") or [{"message": "Shopify no devolvió el fulfillment."}])
    return fulfillment["id"].removeprefix("gid://shopify/Fulfillment/")
