from .process_mercadolibre_order import process_mercadolibre_order


def process_mercadolibre_notification(params, progress_callback=None, cancellation_token=None):
    """Callable registrado en `orchestrator` como
    `orders.process_mercadolibre_notification` -- lo dispara
    `orders/webhooks.py` por cada notificación `orders_v2` válida. Corre en
    segundo plano: el webhook ya respondió `200`.

    `params`: `{"order_id": str}` (ya extraído y validado por
    `parse_mercadolibre_notification`). Contrato de proceso del
    orquestador: `(params, progress_callback=None, cancellation_token=None)`.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    order_id = params["order_id"]
    progress_callback(10, f"Pedido {order_id}")
    outcome = process_mercadolibre_order(order_id)
    progress_callback(100, f"Pedido {order_id}: {outcome}")
