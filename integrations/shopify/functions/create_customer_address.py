from ..client import ShopifyClient
from ..queries import CREATE_CUSTOMER_ADDRESS


class ShopifyCustomerAddressError(Exception):
    """Shopify rechazó la creación de la dirección de forma explícita
    (`userErrors`). Fallo definitivo -- no reintentar sin corregir el dato
    que señala el error."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def create_customer_address(
    *, customer_id, company="", first_name="", last_name="", phone="", set_as_default=True
):
    """Agrega la primera dirección de un cliente que TODAVÍA NO tiene
    ninguna (`customerAddressCreate`). Para un cliente que ya tiene
    dirección, usar `update_customer_address` -- llamar a esta función en
    ese caso puede crear una segunda dirección en vez de corregir la
    existente.

    `customer_id` sin el prefijo `gid://shopify/...` (mismo formato que
    devuelve `create_customer`). `company` es donde vive la cédula/NIT en
    este proyecto (ver docs/implementations-plans/shopify-customers-directory.md).

    Devuelve el `address_id` **tal cual lo entrega Shopify, sin recortar
    ningún prefijo** -- a diferencia del id de Customer/Order/ProductVariant,
    el formato exacto del id de una `MailingAddress` no se confirmó por
    introspección; se trata como opaco y se guarda así en
    `customers.models.ShopifyCustomer.default_address_id`.

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

    response = ShopifyClient().request_graphql(
        CREATE_CUSTOMER_ADDRESS,
        variables={
            "customerId": f"gid://shopify/Customer/{customer_id}",
            "address": address,
            "setAsDefault": set_as_default,
        },
    )
    payload = (response.get("data") or {}).get("customerAddressCreate") or {}
    user_errors = payload.get("userErrors") or []
    result_address = payload.get("address")
    if user_errors or not result_address:
        raise ShopifyCustomerAddressError(
            user_errors or ["Shopify no devolvió la dirección ni un error -- resultado inesperado."]
        )

    return result_address["id"]
