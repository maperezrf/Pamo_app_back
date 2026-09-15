def parse_customer_webhook(payload):
    """Normaliza el payload REST que Shopify manda en los webhooks
    `customers/create`/`customers/update` a la misma forma que usa el resto
    de `customers` (shopify_id, identification, first_name, last_name,
    email, phone, shopify_updated_at).

    NO incluye `default_address_id` en el resultado a propósito -- a
    diferencia del id que devuelve la API GraphQL (con el que sí se puede
    escribir después, ver `create_customer_address.py`), el id de
    dirección de este payload REST es un entero plano y no se puede
    convertir de forma confiable al formato `gid://shopify/MailingAddress/...`
    (que en la API 2024-07 puede incluir además `?model_name=CustomerAddress`,
    no confirmado si aplica siempre). Ese campo lo completa la
    reconciliación periódica, que sí usa GraphQL -- ver
    `upsert_from_shopify_customer.py` para cómo se respeta esa distinción.

    Punto abierto real: la forma exacta de este payload (nombres de campo,
    si `default_address` puede venir `null`, etc.) no se verificó todavía
    contra un webhook real de Shopify -- ver
    docs/implementations-plans/shopify-customers-directory.md. Se asume la
    forma estándar del recurso REST `customer` de Shopify.
    """
    default_address = payload.get("default_address") or {}
    return {
        "shopify_id": str(payload["id"]),
        "identification": default_address.get("company") or "",
        "first_name": payload.get("first_name") or "",
        "last_name": payload.get("last_name") or "",
        "email": payload.get("email") or "",
        "phone": payload.get("phone") or "",
        "shopify_updated_at": payload.get("updated_at"),
    }
