from ..models import ShopifyCustomer


def upsert_from_shopify_customer(data):
    """Crea o actualiza la fila local a partir de un cliente ya normalizado
    de Shopify (misma forma que devuelven
    `integrations.shopify.functions.list_customers_page` y
    `parse_customer_webhook`: shopify_id, identification, first_name,
    last_name, email, phone, default_address_id, shopify_updated_at).

    Punto único de "cómo se guarda un cliente" -- lo usan tanto el webhook
    (un cliente a la vez) como la reconciliación periódica (uno por cada
    cliente de la página), para no duplicar esta lógica en dos lugares.

    `default_address_id` es un caso especial: el payload REST del webhook
    no puede convertir de forma confiable su id de dirección al formato
    GraphQL que usan `create_customer_address`/`update_customer_address`
    (ver `parse_customer_webhook.py`), así que `parse_customer_webhook`
    directamente **no incluye esa clave** en el dict que produce. Por eso
    acá se distingue "la clave no vino" (no tocar el valor que ya había,
    típicamente puesto por la reconciliación) de "vino vacía" (sí
    sobrescribir -- la reconciliación confirma que el cliente no tiene
    dirección). `list_customers_page` (reconciliación) siempre incluye la
    clave, aunque sea `""`.

    Devuelve la instancia de `ShopifyCustomer` creada o actualizada.
    """
    defaults = {
        "identification": data.get("identification", ""),
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "email": data.get("email", ""),
        "phone": data.get("phone", ""),
        "shopify_updated_at": data.get("shopify_updated_at"),
    }
    if "default_address_id" in data:
        defaults["default_address_id"] = data["default_address_id"]

    customer, _created = ShopifyCustomer.objects.update_or_create(
        shopify_id=data["shopify_id"], defaults=defaults
    )
    return customer
