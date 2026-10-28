from config.constants import SODIMAC_SHOPIFY_CUSTOMER_ID

from ..models import MarketplaceOrder
from .claim_orders import RETRYABLE_STATUSES
from .sync_sodimac_orders import create_sodimac_order

SODIMAC = MarketplaceOrder.Marketplace.SODIMAC


class RetryOrderNotFound(Exception):
    pass


class RetryOrderError(Exception):
    """La OC no se puede reintentar; el texto es para el equipo."""


class RetryOrderBusy(Exception):
    """Otro proceso la está creando (o se quedó a mitad): no se reintenta."""


def retry_marketplace_order(order_id):
    """Botón "Reintentar" de los pedidos no creados en Shopify (desde
    2026-10-28, decisión del usuario): después de corregir la equivalencia
    del SKU en Productos, crea la OC en Shopify sin esperar al próximo sync.
    Por ahora solo Sodimac.

    Devuelve la fila con el resultado: `orden_creada`, o
    `error_creando_orden` con el motivo (p. ej. el SKU sigue sin
    equivalencia). `RetryOrderNotFound`, `RetryOrderError` (no aplica) o
    `RetryOrderBusy` (en `procesando`: puede estar creándose ahora o haberse
    quedado a mitad; nunca se reintenta a ciegas). Un fallo de Shopify se
    propaga y la OC queda en `procesando` (revisión manual)."""
    row = MarketplaceOrder.objects.filter(pk=order_id).first()
    if row is None:
        raise RetryOrderNotFound()
    if row.marketplace != SODIMAC:
        raise RetryOrderError("Por ahora solo se reintentan desde aquí las OC de Sodimac.")
    if row.shopify_order_id:
        raise RetryOrderError(f"La OC ya tiene orden en Shopify ({row.shopify_order_name or row.shopify_order_id}).")
    if row.status == MarketplaceOrder.Status.PROCESSING:
        raise RetryOrderBusy(
            "La OC está en proceso: se está creando o el proceso murió a mitad. Revisar en Shopify antes de reintentar."
        )
    if row.status not in RETRYABLE_STATUSES:
        raise RetryOrderError(f"La OC está en {row.get_status_display()} y no se reintenta.")
    if not SODIMAC_SHOPIFY_CUSTOMER_ID:
        raise RetryOrderError("SODIMAC_SHOPIFY_CUSTOMER_ID no está configurado.")
    result = create_sodimac_order(row.marketplace_order_id, SODIMAC_SHOPIFY_CUSTOMER_ID)
    if result is None:
        raise RetryOrderBusy("Otro proceso tomó la OC al mismo tiempo; revisar en un momento.")
    return result
