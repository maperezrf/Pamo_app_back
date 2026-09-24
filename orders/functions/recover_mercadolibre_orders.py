from collections import Counter
from datetime import timedelta

from django.utils import timezone

from integrations.mercadolibre.functions.get_missed_feeds import get_missed_feeds

from ..models import MarketplaceOrder
from .parse_mercadolibre_notification import TOPIC, order_id_from_resource
from .process_mercadolibre_order import INCOMPLETE_PREFIX, RETRYABLE_STATUSES, process_mercadolibre_order

# Un pedido pendiente más reciente que esto puede estar procesándolo un
# webhook en este momento; se deja para la siguiente corrida.
STALE_AFTER = timedelta(minutes=15)
# Un envío de pack que sigue incompleto después de esto se reporta.
INCOMPLETE_REPORT_AFTER = timedelta(hours=6)


def recover_mercadolibre_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso programado `orders.recover_mercadolibre`: rescata pedidos de
    Mercado Libre que no llegaron a Shopify por el webhook (decisión G de
    docs/implementations-plans/mercadolibre-orders-import.md):

    1. Avisos no entregados: `missed_feeds` del tópico `orders_v2`.
    2. Avisos entregados cuyo proceso falló, y
    3. pedidos guardados con `error_creando_orden`: en ambos casos queda un
       `MarketplaceOrder` de Mercado Libre sin `shopify_order_id` en
       `pending`/`error_creando_orden` (el proceso deja un marcador antes
       de llamar a Mercado Libre), con más de `STALE_AFTER` sin cambios.

    Cada pedido pasa por `process_mercadolibre_order`, idempotente. Los
    pedidos en `procesando` nunca se tocan (revisión manual). Un fallo en
    un pedido no detiene el resto; al final, si hubo fallos, la ejecución
    termina en error con el resumen. También reporta envíos de pack que
    siguen incompletos tras `INCOMPLETE_REPORT_AFTER`.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)

    progress_callback(5, "Leyendo notificaciones no entregadas")
    order_ids = [order_id_from_resource(message.get("resource")) for message in get_missed_feeds(TOPIC)]
    missed_count = len([order_id for order_id in order_ids if order_id])

    now = timezone.now()
    order_ids += list(
        MarketplaceOrder.objects.filter(
            marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE,
            shopify_order_id="",
            status__in=RETRYABLE_STATUSES,
            updated_at__lt=now - STALE_AFTER,
        )
        .order_by("created_at")
        .values_list("marketplace_order_id", flat=True)
    )
    order_ids = list(dict.fromkeys(order_id for order_id in order_ids if order_id))
    progress_callback(15, f"{len(order_ids)} pedidos por revisar ({missed_count} de notificaciones no entregadas)")

    outcomes = Counter()
    failures = []
    total = len(order_ids) or 1
    for index, order_id in enumerate(order_ids):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        try:
            outcomes[process_mercadolibre_order(order_id)] += 1
        except Exception as error:  # un pedido no detiene el resto
            failures.append(f"{order_id} ({type(error).__name__}: {error})")
        progress_callback(15 + int(80 * (index + 1) / total), f"Pedido {order_id}")

    stuck = list(
        MarketplaceOrder.objects.filter(
            marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE,
            shopify_order_id="",
            error_description__startswith=INCOMPLETE_PREFIX,
            created_at__lt=now - INCOMPLETE_REPORT_AFTER,
        ).values_list("marketplace_order_id", flat=True)
    )

    summary = f"{len(order_ids)} revisados: {dict(outcomes)}"
    if stuck:
        summary += f"; envíos incompletos hace más de {INCOMPLETE_REPORT_AFTER}: {', '.join(stuck)}"
    if failures:
        raise RuntimeError(f"{summary}; fallaron {len(failures)}: {'; '.join(failures)}")
    progress_callback(100, summary)
