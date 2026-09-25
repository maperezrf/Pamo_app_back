from django.db import transaction
from django.utils import timezone

from ..models import MarketplaceOrder

# Estados desde los que un pedido sin `shopify_order_id` puede intentarse
# (o reintentarse) hacia Shopify.
RETRYABLE_STATUSES = (MarketplaceOrder.Status.PENDING, MarketplaceOrder.Status.ERROR_ORDER)


def claim_orders(marketplace, order_ids):
    """Pasa a `procesando` TODAS las órdenes `order_ids` del marketplace o
    ninguna, bajo bloqueo de fila. Si alguna no existe o ya no está
    pendiente/en error (otro proceso la tomó o ya se creó), devuelve None;
    si no, devuelve las filas reclamadas.

    Es lo que evita crear dos órdenes en Shopify cuando dos procesos
    (webhook y recuperación, o dos avisos) llegan a la vez al mismo pedido.
    Una fila que se queda en `procesando` no se reintenta sola: se revisa a
    mano (docs/apps/orders.md).
    """
    with transaction.atomic():
        rows = list(
            MarketplaceOrder.objects.select_for_update()
            .filter(marketplace=marketplace, marketplace_order_id__in=order_ids)
            .order_by("pk")
        )
        if len(rows) != len(set(order_ids)):
            return None
        if any(row.shopify_order_id or row.status not in RETRYABLE_STATUSES for row in rows):
            return None
        MarketplaceOrder.objects.filter(pk__in=[row.pk for row in rows]).update(
            status=MarketplaceOrder.Status.PROCESSING, updated_at=timezone.now()
        )
        for row in rows:
            row.status = MarketplaceOrder.Status.PROCESSING
    return rows
