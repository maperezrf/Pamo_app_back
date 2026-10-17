import re

FULFILLMENT_ORDER_GID = re.compile(r"^gid://shopify/FulfillmentOrder/(\d+)$")

# Un aviso real trae dos o tres; el tope evita recorrer un payload absurdo.
MAX_FULFILLMENT_ORDER_IDS = 20


def parse_fulfillment_order_webhook(payload):
    """Ids de fulfillment order (sin `gid://`, sin repetir, en orden de
    aparición) que menciona un aviso `fulfillment_orders/*` de Shopify.

    Cada tema trae otra forma (`moved`: original y movido; `split`:
    original, restante y reemplazo; `order_routing_complete`: uno), pero
    ninguno trae el pedido. Por eso se recorre todo el payload buscando
    gids de `FulfillmentOrder` y claves `fulfillment_order_id`: el proceso
    solo los usa para encontrar el pedido y lo vuelve a leer de Shopify.
    """
    found = []

    def add(value):
        if value and value not in found and len(found) < MAX_FULFILLMENT_ORDER_IDS:
            found.append(value)

    def walk(node, key=None):
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, child_key)
        elif isinstance(node, list):
            for child in node:
                walk(child, key)
        elif isinstance(node, str):
            match = FULFILLMENT_ORDER_GID.match(node)
            if match:
                add(match.group(1))
            elif key == "fulfillment_order_id" and node.isdigit():
                add(node)
        elif isinstance(node, int) and not isinstance(node, bool) and key == "fulfillment_order_id":
            add(str(node))

    walk(payload)
    return found
