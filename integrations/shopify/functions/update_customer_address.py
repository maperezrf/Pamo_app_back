from ..client import ShopifyClient
from ..queries import UPDATE_CUSTOMER_ADDRESS


class ShopifyCustomerAddressError(Exception):
    """Shopify rechazó la actualización de la dirección de forma explícita
    (`userErrors`). Fallo definitivo -- no reintentar sin corregir el dato
    que señala el error."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def update_customer_address(
    *, customer_id, address_id, company="", first_name="", last_name="", phone="", set_as_default=True
):
    """Actualiza una dirección que un cliente YA tiene (`customerAddressUpdate`)
    -- es el método para escribir/corregir la cédula (`company`) de un
    cliente existente. Para un cliente sin ninguna dirección todavía, usar
    `create_customer_address`.

    `customer_id` sin el prefijo `gid://shopify/...`. `address_id` se pasa
    **tal cual** se recibió de Shopify (de `create_customer_address` o de
    `defaultAddress.id` en una consulta) -- no se le agrega ni se le quita
    ningún prefijo, se trata como opaco (ver nota en `create_customer_address.py`).

    Solo se mandan los campos de `address` que vengan con valor -- no se
    borra un campo existente en Shopify que esta llamada no mencione
    (comportamiento de `customerAddressUpdate`: actualización parcial).

    Lanza `ShopifyCustomerAddressError` si Shopify rechaza la operación
    explícitamente.
    """
    address = {}
    if company:
        address["company"] = company
    if first_name:
        address["firstName"] = first_name
    if last_name:
        address["lastName"] = last_name
    if phone:
        address["phone"] = phone
    if not address:
        raise ValueError("no se dio ningún campo de la dirección para actualizar")

    response = ShopifyClient().request_graphql(
        UPDATE_CUSTOMER_ADDRESS,
        variables={
            "customerId": f"gid://shopify/Customer/{customer_id}",
            "addressId": address_id,
            "address": address,
            "setAsDefault": set_as_default,
        },
    )
    payload = (response.get("data") or {}).get("customerAddressUpdate") or {}
    user_errors = payload.get("userErrors") or []
    result_address = payload.get("address")
    if user_errors or not result_address:
        raise ShopifyCustomerAddressError(
            user_errors or ["Shopify no devolvió la dirección ni un error -- resultado inesperado."]
        )

    return result_address["id"]
