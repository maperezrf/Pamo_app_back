from config.constants import FALABELLA_SHOPIFY_CUSTOMER_ID

from ..models import MarketplaceOrder
from .process_shipment import process_shipment


def process_pending_orders(progress_callback=None, cancellation_token=None, limit=None):
    """Por cada MarketplaceOrder **de Falabella** sin `shopify_order_id`
    (nuevo o de un error anterior -- mismo criterio, sin distinción, ver
    docs/implementations-plans/marketplace-orders-import.md), crea la orden
    en Shopify a nombre del cliente fijo (`FALABELLA_SHOPIFY_CUSTOMER_ID`)
    con `process_shipment` (resolución de SKU, bodega y `create_order`; un
    pedido de Falabella = un envío). Los datos reales del comprador quedan
    en el MarketplaceOrder y en el `note` de la orden -- ver
    docs/implementations-plans/falabella-fixed-customer.md.

    Solo Falabella: Mercado Libre llega por webhook y tiene su propio flujo
    (orders/functions/process_mercadolibre_order.py).

    Un pedido que falla queda marcado con su estado de error y no detiene
    el resto del lote. Sin cliente fijo configurado, falla antes de tocar
    cualquier pedido.

    `limit`: si se da, procesa como máximo esa cantidad de pedidos
    pendientes (los más antiguos primero) -- pensado para una primera
    corrida controlada contra la cuenta real (ej. `limit=1`), no para uso
    normal. `None` procesa todos los pendientes.
    """
    if not FALABELLA_SHOPIFY_CUSTOMER_ID:
        raise ValueError("FALABELLA_SHOPIFY_CUSTOMER_ID no está configurado")

    progress_callback = progress_callback or (lambda percent, step=None: None)
    pending_queryset = (
        MarketplaceOrder.objects.filter(marketplace=MarketplaceOrder.Marketplace.FALABELLA, shopify_order_id="")
        .order_by("created_at")
        .prefetch_related("items")
    )
    if limit:
        pending_queryset = pending_queryset[:limit]
    pending = list(pending_queryset)
    progress_callback(5, f"{len(pending)} pedidos pendientes")

    total = len(pending) or 1
    for index, order in enumerate(pending):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        process_shipment([order], FALABELLA_SHOPIFY_CUSTOMER_ID)
        progress_callback(
            5 + int(90 * (index + 1) / total),
            f"Pedido {order.marketplace_order_number or order.marketplace_order_id}",
        )

    progress_callback(100, f"{len(pending)} pedidos pendientes procesados")
