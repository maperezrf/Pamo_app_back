from .parse_customer_webhook import parse_customer_webhook
from .upsert_from_shopify_customer import upsert_from_shopify_customer


def process_customer_webhook(params, progress_callback=None, cancellation_token=None):
    """Callable registrado en `orchestrator` como `customers.process_webhook`
    -- lo dispara `customers/webhooks.py` para cada evento
    `customers/create`/`customers/update` ya verificado por firma. Corre en
    segundo plano (ver docs/patterns/PROVIDER_WEBHOOKS.md): el endpoint ya
    respondió antes de que esto se ejecute.

    `params`: `{"topic": str, "payload": dict}` -- el payload ya parseado
    del JSON del webhook. Contrato de proceso del orquestador:
    `(params, progress_callback=None, cancellation_token=None)`.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    progress_callback(10, "Parseando payload")
    parsed = parse_customer_webhook(params["payload"])
    progress_callback(60, "Guardando en el directorio local")
    upsert_from_shopify_customer(parsed)
    progress_callback(100, "Completado")
