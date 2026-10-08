import logging

from ..models import Dispatch, DispatchLocation, ShopifyOrder
from .dispatch_orders import ensure_dispatch
from .shopify_order_marketplace import SHOPIFY_CHANNEL

logger = logging.getLogger(__name__)

PAID = "PAID"


def assign_web_dispatch(shopify_id):
    """Asigna bodega a un pedido de la **tienda web** (también Addi y
    cotizaciones: `marketplace="shopify"`) apenas está pagado, creando su
    `Dispatch` con `assign_dispatch_location` (misma regla de inventario que
    los marketplaces). No avisa a nadie ni pide guía.

    Lo llaman el webhook de pedidos (al instante) y la reconciliación (red
    de seguridad); el backfill no, para no asignar históricos. Los pedidos de
    marketplace no pasan por aquí: ya traen su bodega de la importación, y el
    webhook puede llegar antes de que se guarde su vínculo con
    `MarketplaceOrder`.

    Una sola vez: si ya tiene despacho no se recalcula. Solo pedidos
    pagados, sin cancelar ni despachar (regla del 2026-10-08: una cotización
    entra solo si se vuelve pedido y está pagada). Un error (inventario de
    Shopify caído) no interrumpe a quien llama: se registra y la
    reconciliación lo reintenta. Tampoco asigna mientras el registro de
    bodegas esté vacío (sin `sync_dispatch_locations`): el pedido quedaría
    en `manual` para siempre por "bodega sin registrar".

    Devuelve el `Dispatch` creado o None.
    """
    order = (
        ShopifyOrder.objects.filter(shopify_id=str(shopify_id), deleted_at__isnull=True)
        .prefetch_related("lines")
        .first()
    )
    if (
        order is None
        or order.marketplace != SHOPIFY_CHANNEL
        or order.financial_status != PAID
        or order.cancelled_at
        or order.fulfillment_status == "FULFILLED"
        or Dispatch.objects.filter(shopify_order=order).exists()
        or not DispatchLocation.objects.exists()
    ):
        return None
    try:
        return ensure_dispatch(order)
    except Exception:  # noqa: BLE001 -- la reconciliación lo reintenta
        logger.exception("No se pudo asignar bodega al pedido web %s", order.name)
        return None
