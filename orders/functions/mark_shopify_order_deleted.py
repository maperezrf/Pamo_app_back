from django.utils import timezone

from ..models import ShopifyOrder


def mark_shopify_order_deleted(shopify_id):
    """Borrado lógico de la copia local (`deleted_at`). Si el pedido no
    existe localmente, deja una marca borrada mínima, para que un
    `orders/updated` que llegue después no lo cree. Idempotente."""
    now = timezone.now()
    if not ShopifyOrder.objects.filter(shopify_id=shopify_id, deleted_at__isnull=True).update(deleted_at=now):
        ShopifyOrder.objects.get_or_create(shopify_id=shopify_id, defaults={"deleted_at": now})
