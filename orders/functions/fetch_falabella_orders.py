from datetime import datetime, timedelta, timezone

from integrations.falabella.functions.get_order_items import get_order_items
from integrations.falabella.functions.get_orders import get_orders

from ..models import MarketplaceOrder, MarketplaceOrderItem

# Ventana con solape a propósito -- no hay checkpoint de "hasta cuándo se
# sincronizó"; el UniqueConstraint(marketplace, marketplace_order_id) es lo
# que evita duplicar un pedido que ya se trajo en una corrida anterior.
LOOKBACK = timedelta(hours=48)


def fetch_falabella_orders(progress_callback=None, cancellation_token=None, created_after=None):
    """Trae pedidos de Falabella y persiste localmente los que todavía no
    existan -- no toca Shopify para nada acá. Un pedido que ya existe
    localmente NO se actualiza (process_pending_orders es quien decide qué
    hacer con los pendientes; esta función solo descubre pedidos nuevos).

    `created_after`: desde cuándo traer pedidos. Si no se da, usa las
    últimas 48h (con solape a propósito -- no hay checkpoint de "hasta
    cuándo se sincronizó"; el `UniqueConstraint(marketplace,
    marketplace_order_id)` es lo que evita duplicar un pedido ya traído).
    Acepta un `datetime` con zona horaria, o un string ISO-8601 (así llega
    si viene de `params` del orquestador, que es JSON) -- ej. "hoy en
    adelante" se pasa como `created_after="2026-09-22T00:00:00+00:00"`.

    Devuelve la cantidad de pedidos nuevos creados.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    created_before = datetime.now(timezone.utc)
    if isinstance(created_after, str):
        created_after = datetime.fromisoformat(created_after)
    if created_after is None:
        created_after = created_before - LOOKBACK

    raw_orders = get_orders(created_after=created_after, created_before=created_before, limit=100)
    progress_callback(10, f"{len(raw_orders)} pedidos de Falabella en el rango")

    new_count = 0
    total = len(raw_orders) or 1
    for index, raw_order in enumerate(raw_orders):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()

        marketplace_order, created = MarketplaceOrder.objects.get_or_create(
            marketplace=MarketplaceOrder.Marketplace.FALABELLA,
            marketplace_order_id=str(raw_order["order_id"]),
            defaults={
                "marketplace_order_number": raw_order["order_number"],
                "customer_identification": raw_order["customer_identification"],
                "customer_first_name": raw_order["customer_first_name"],
                "customer_last_name": raw_order["customer_last_name"],
                "customer_email": raw_order["customer_email"],
            },
        )
        if created:
            new_count += 1
            items = get_order_items(raw_order["order_id"])
            MarketplaceOrderItem.objects.bulk_create(
                [
                    MarketplaceOrderItem(
                        order=marketplace_order,
                        marketplace_sku=item["sku"],
                        quantity=item["quantity"],
                        unit_price=item["price"],
                    )
                    for item in items
                ]
            )

        progress_callback(10 + int(80 * (index + 1) / total), f"Pedido {raw_order['order_number']}")

    progress_callback(100, f"{new_count} pedidos nuevos de {len(raw_orders)} revisados")
    return new_count
