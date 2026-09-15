from ..client import ShopifyClient
from ..constants import FINANCIAL_STATUSES
from ..queries import CREATE_ORDER


class ShopifyOrderCreationError(Exception):
    """Shopify rechazó la orden de forma explícita (`userErrors`). Fallo
    definitivo -- no reintentar con los mismos datos sin corregir el
    problema que indica el error."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def create_order(*, items, customer_id, financial_status, note="", tags=None, currency="COP"):
    """Crea un pedido en Shopify vía GraphQL (`orderCreate`).

    `items`: lista de {"variant_id": str, "quantity": int, "price": str}.
    `variant_id` es el id SIN el prefijo `gid://...` -- el mismo formato
    que devuelve `get_variant_by_sku`.
    `customer_id`: id numérico del cliente en Shopify, también sin prefijo.
    `financial_status`: uno de FINANCIAL_STATUSES (ej. "PENDING", "PAID").

    Devuelve {"order_id": str, "order_name": str} (id sin prefijo) si
    Shopify confirma la creación.

    Lanza `ShopifyOrderCreationError` si Shopify rechaza la orden
    explícitamente -- no reintentar sin corregir el dato señalado.

    Si la llamada falla por red/timeout o por un error GraphQL de
    transporte (`ShopifyGraphQLError`), NO se sabe si la orden quedó
    creada en Shopify: esta función no reintenta internamente, y quien la
    llame tampoco debe reintentarla a ciegas -- primero hay que verificar
    en Shopify si la orden ya existe. `orderCreate` no tiene una clave de
    idempotencia propia en el schema.
    """
    if financial_status not in FINANCIAL_STATUSES:
        raise ValueError(f"financial_status inválido: {financial_status!r}")

    order_input = {
        "lineItems": [
            {
                "variantId": f"gid://shopify/ProductVariant/{item['variant_id']}",
                "quantity": int(item["quantity"]),
                "priceSet": {
                    "shopMoney": {"amount": str(item["price"]), "currencyCode": currency}
                },
            }
            for item in items
        ],
        "customer": {"toAssociate": {"id": f"gid://shopify/Customer/{customer_id}"}},
        "financialStatus": financial_status,
        "note": note,
    }
    if tags:
        order_input["tags"] = list(tags)

    response = ShopifyClient().request_graphql(CREATE_ORDER, variables={"order": order_input})
    payload = (response.get("data") or {}).get("orderCreate") or {}
    user_errors = payload.get("userErrors") or []
    order = payload.get("order")
    if user_errors or not order:
        raise ShopifyOrderCreationError(
            user_errors or ["Shopify no devolvió la orden ni un error -- resultado inesperado."]
        )

    return {
        "order_id": order["id"].removeprefix("gid://shopify/Order/"),
        "order_name": order["name"],
    }
