import re

TOPIC = "orders_v2"
_ORDER_RESOURCE = re.compile(r"^/orders/(\d+)$")


def order_id_from_resource(resource):
    """`"/orders/123"` -> `"123"`; cualquier otra cosa -> `None`."""
    match = _ORDER_RESOURCE.match(str(resource or ""))
    return match.group(1) if match else None


def parse_mercadolibre_notification(payload, *, application_id, seller_id):
    """Valida una notificación de Mercado Libre y devuelve el id del pedido
    a procesar, o `None` si no hay nada que hacer.

    Mercado Libre no firma sus notificaciones (ver
    docs/patterns/PROVIDER_WEBHOOKS.md), así que el payload nunca se usa
    como dato: solo se extrae el id y el pedido se lee de la API con el
    token propio. Se exige que sea del tópico `orders_v2`, de esta app
    (`application_id`) y de la cuenta conectada (`user_id` = `seller_id`).
    Sin `application_id` o `seller_id` configurados, nada es válido.
    """
    if not isinstance(payload, dict) or not application_id or not seller_id:
        return None
    if payload.get("topic") != TOPIC:
        return None
    if str(payload.get("application_id", "")) != str(application_id):
        return None
    if str(payload.get("user_id", "")) != str(seller_id):
        return None
    return order_id_from_resource(payload.get("resource"))
