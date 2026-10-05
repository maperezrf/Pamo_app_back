from integrations.shopify.functions.get_order import get_order

from .mark_shopify_order_deleted import mark_shopify_order_deleted
from .upsert_shopify_order import upsert_shopify_order

DELETE_TOPIC = "orders/delete"


def process_shopify_order_webhook(params, progress_callback=None, cancellation_token=None):
    """Callable registrado en `orchestrator` como
    `orders.process_shopify_order_webhook` (`allow_concurrent=True`). Lo
    lanza `orders/webhooks.py` por cada aviso `orders/create`,
    `orders/updated` u `orders/delete` ya verificado por firma.

    `params`: `{"topic", "order_id"}`. El payload del aviso no se usa como
    dato: el pedido se vuelve a leer de Shopify (`get_order`), así un aviso
    repetido o desordenado deja siempre el estado actual. Si Shopify ya no
    lo encuentra, se trata como borrado.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    order_id = params["order_id"]
    if params.get("topic") == DELETE_TOPIC:
        mark_shopify_order_deleted(order_id)
        progress_callback(100, f"Pedido {order_id} marcado como borrado")
        return

    progress_callback(10, f"Leyendo pedido {order_id} de Shopify")
    data = get_order(order_id)
    if data is None:
        mark_shopify_order_deleted(order_id)
        progress_callback(100, f"Pedido {order_id} no existe en Shopify: marcado como borrado")
        return
    result = upsert_shopify_order(data)
    progress_callback(100, f"Pedido {data['name'] or order_id}: {result}")
