from integrations.shopify.functions.create_customer_address import create_customer_address
from integrations.shopify.functions.update_customer_address import update_customer_address


def set_identification(shopify_customer, identification, *, first_name="", last_name="", phone=""):
    """Escribe/actualiza la cédula (`company`) de un cliente de Shopify que
    ya está en el directorio local -- el método de escritura que expone
    `customers` al resto del sistema (`orders` y canales futuros).

    Decide sola cuál mutación de Shopify usar: si `shopify_customer` ya
    tiene `default_address_id`, actualiza esa dirección
    (`update_customer_address`); si no, crea la primera
    (`create_customer_address`).

    `shopify_customer`: instancia de `customers.models.ShopifyCustomer` que
    ya existe localmente -- si el cliente todavía no existe ni en Shopify
    ni acá, crearlo primero con
    `integrations.shopify.functions.create_customer.create_customer` y
    hacer upsert local antes de llamar a esta función.

    Actualiza la fila local con el resultado en la misma llamada -- no
    espera al webhook para reflejar el cambio.

    Devuelve la instancia `shopify_customer` actualizada.
    """
    if shopify_customer.default_address_id:
        update_customer_address(
            customer_id=shopify_customer.shopify_id,
            address_id=shopify_customer.default_address_id,
            company=identification,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
    else:
        address_id = create_customer_address(
            customer_id=shopify_customer.shopify_id,
            company=identification,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
        shopify_customer.default_address_id = address_id

    shopify_customer.identification = identification
    shopify_customer.save(update_fields=["identification", "default_address_id", "synced_at"])
    return shopify_customer
