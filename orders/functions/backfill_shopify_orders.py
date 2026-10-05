from datetime import date, datetime, time, timedelta

from django.utils import timezone

from ..models import ShopifyOrderSyncState
from .order_listing_format import COLOMBIA_TZ
from .sync_shopify_orders import iter_shopify_order_pages, shopify_datetime, summary

DEFAULT_DAYS = 30


def backfill_shopify_orders(params=None, progress_callback=None, cancellation_token=None):
    """Carga inicial de la copia local: trae de Shopify los pedidos creados
    desde `params["created_from"]` (`YYYY-MM-DD`, día de Colombia; por
    defecto hoy menos 30 días) y los guarda con `upsert_shopify_order`.
    Repetirla no duplica nada.

    Registrado en `orchestrator` como `orders.backfill_shopify_orders`
    (`allow_concurrent=False`, ejecución manual). Si la reconciliación nunca
    corrió, deja su checkpoint en la hora de inicio de esta carga, para que
    siga desde ahí.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    started_at = timezone.now()

    created_from = params.get("created_from")
    day = date.fromisoformat(created_from) if created_from else timezone.localdate() - timedelta(days=DEFAULT_DAYS)
    since = datetime.combine(day, time.min, tzinfo=COLOMBIA_TZ)

    counts = None
    for page_number, counts in iter_shopify_order_pages(f"created_at:>={shopify_datetime(since)}"):
        # Sin total de páginas conocido: se limita a 99 hasta terminar.
        progress_callback(min(99, page_number), f"Página {page_number} -- {summary(counts)}")
        if cancellation_token:
            cancellation_token.raise_if_cancelled()

    ShopifyOrderSyncState.get()
    ShopifyOrderSyncState.objects.filter(pk=1, last_reconciled_at__isnull=True).update(last_reconciled_at=started_at)
    progress_callback(100, f"Completado desde {day.isoformat()} -- {summary(counts)}")
