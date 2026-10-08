from datetime import timedelta

from django.utils import timezone

from ..models import ShopifyOrderSyncState
from .sync_shopify_orders import iter_shopify_order_pages, shopify_datetime, summary

OVERLAP = timedelta(minutes=15)
FIRST_RUN_WINDOW = timedelta(days=30)


def reconcile_shopify_orders(params=None, progress_callback=None, cancellation_token=None):
    """Red de seguridad de los webhooks de pedidos: trae de Shopify los
    pedidos actualizados desde el último checkpoint (menos 15 minutos de
    solape) y los guarda con `upsert_shopify_order`. Sin checkpoint, toma
    los últimos 30 días, así que su primera corrida también hace de carga
    inicial.

    Registrado en `orchestrator` como `orders.reconcile_shopify_orders`
    (`allow_concurrent=False`, programado). El checkpoint
    (`ShopifyOrderSyncState.last_reconciled_at`) avanza a la hora de inicio
    **solo si la corrida termina bien**: si falla o se cancela, la próxima
    repite la ventana.

    No detecta pedidos borrados: Shopify no los lista. Eso depende del
    webhook `orders/delete`. También asigna bodega a los pedidos web pagados
    sin despacho (`assign_web_dispatch`), por si se perdió el webhook.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    started_at = timezone.now()
    state = ShopifyOrderSyncState.get()
    since = state.last_reconciled_at - OVERLAP if state.last_reconciled_at else started_at - FIRST_RUN_WINDOW

    counts = None
    query = f"updated_at:>={shopify_datetime(since)}"
    for page_number, counts in iter_shopify_order_pages(query, assign_web_dispatches=True):
        progress_callback(min(99, page_number), f"Página {page_number} -- {summary(counts)}")
        if cancellation_token:
            cancellation_token.raise_if_cancelled()

    ShopifyOrderSyncState.objects.filter(pk=1).update(last_reconciled_at=started_at)
    progress_callback(100, f"Completado -- {summary(counts)}")
