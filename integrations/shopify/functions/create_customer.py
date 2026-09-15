from ..client import ShopifyClient
from ..queries import CREATE_CUSTOMER


class ShopifyCustomerCreationError(Exception):
    """Shopify rechazó la creación del cliente de forma explícita
    (`userErrors`). Fallo definitivo -- no reintentar con los mismos datos
    sin corregir el problema que indica el error."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def create_customer(*, email="", phone="", first_name="", last_name=""):
    """Crea un cliente en Shopify vía GraphQL (`customerCreate`).

    No fija ninguna dirección -- `CustomerInput` no tiene ese campo
    (verificado por introspección el 2026-09-15). Para guardar la cédula en
    `company`, llamar después a `create_customer_address`.

    Se exige al menos `email` o `phone` como validación defensiva propia
    (no confirmado si Shopify lo exige a nivel de negocio; el schema no lo
    marca como obligatorio) -- evita mandar un cliente sin ninguna forma de
    identificarlo.

    Devuelve el id del cliente sin el prefijo `gid://shopify/...` (mismo
    formato que usa `create_order.py` para asociar el cliente al pedido).

    Lanza `ShopifyCustomerCreationError` si Shopify rechaza la creación
    explícitamente.
    """
    if not email and not phone:
        raise ValueError("se necesita al menos email o phone")

    input_data = {"firstName": first_name, "lastName": last_name}
    if email:
        input_data["email"] = email
    if phone:
        input_data["phone"] = phone

    response = ShopifyClient().request_graphql(CREATE_CUSTOMER, variables={"input": input_data})
    payload = (response.get("data") or {}).get("customerCreate") or {}
    user_errors = payload.get("userErrors") or []
    customer = payload.get("customer")
    if user_errors or not customer:
        raise ShopifyCustomerCreationError(
            user_errors or ["Shopify no devolvió el cliente ni un error -- resultado inesperado."]
        )

    return customer["id"].removeprefix("gid://shopify/Customer/")
