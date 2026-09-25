from collections import Counter
from datetime import timedelta

from django.utils import timezone

from integrations.madecentro.functions.list_orders import list_orders

from ..models import MarketplaceOrder
from .claim_orders import RETRYABLE_STATUSES
from .process_madecentro_order import PAID, process_madecentro_order

DEFAULT_DAYS = 1
PAGE_SIZE = 100
# Tope de seguridad: con PAGE_SIZE=100 son 5.000 pedidos por corrida.
MAX_PAGES = 50


def import_madecentro_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso registrado en orchestrator como `orders.import_madecentro`:
    trae los pedidos pagados de Madecentro de los últimos `days` días y
    crea en Shopify los que falten, sin duplicar. También reintenta los
    pedidos de Madecentro guardados en `pending`/`error_creando_orden`,
    aunque ya estén fuera del rango. Sirve de importación mientras no hay
    webhook y, después, de recuperación. Ver
    docs/implementations-plans/madecentro-orders-import.md.

    `params` opcional:
    - `"days"`: cuántos días hacia atrás (por defecto 1: de ayer a hoy, en
      hora de Colombia; Shipturtle filtra por día).
    - `"limit"`: máximo de pedidos a procesar en la corrida (primera
      corrida controlada, ej. `{"limit": 1}`).

    Un fallo en un pedido no detiene el resto; al final, si hubo fallos,
    la ejecución termina en error con el resumen.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    days = int(params.get("days") or DEFAULT_DAYS)
    limit = params.get("limit")

    today = timezone.localdate()
    start_date = today - timedelta(days=days)
    progress_callback(5, f"Listando pedidos de Madecentro del {start_date} al {today}")
    order_ids = _paid_order_ids(start_date, today, cancellation_token)
    listed_count = len(order_ids)

    order_ids += list(
        MarketplaceOrder.objects.filter(
            marketplace=MarketplaceOrder.Marketplace.MADECENTRO,
            shopify_order_id="",
            status__in=RETRYABLE_STATUSES,
        )
        .order_by("created_at")
        .values_list("marketplace_order_id", flat=True)
    )
    order_ids = list(dict.fromkeys(order_ids))
    if limit:
        order_ids = order_ids[: int(limit)]
    progress_callback(20, f"{len(order_ids)} pedidos por revisar ({listed_count} pagados en el rango)")

    outcomes = Counter()
    failures = []
    total = len(order_ids) or 1
    for index, order_id in enumerate(order_ids):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        try:
            outcomes[process_madecentro_order(order_id)] += 1
        except Exception as error:  # un pedido no detiene el resto
            failures.append(f"{order_id} ({type(error).__name__}: {error})")
        progress_callback(20 + int(75 * (index + 1) / total), f"Pedido {order_id}")

    summary = f"{len(order_ids)} revisados: {dict(outcomes)}"
    if failures:
        raise RuntimeError(f"{summary}; fallaron {len(failures)}: {'; '.join(failures)}")
    progress_callback(100, summary)


def _paid_order_ids(start_date, end_date, cancellation_token):
    """Ids de los pedidos pagados del rango, recorriendo todas las páginas.
    El listado no trae SKU ni precio: solo sirve para descubrirlos."""
    order_ids = []
    seen = 0
    for page in range(1, MAX_PAGES + 1):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        response = list_orders(start_date, end_date, page=page, limit=PAGE_SIZE)
        seen += len(response["orders"])
        order_ids += [order["order_id"] for order in response["orders"] if order["financial_status"] == PAID]
        if not response["orders"] or seen >= response["count"]:
            break
    return order_ids
