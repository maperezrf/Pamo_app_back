from django.db.models import Q
from django.utils import timezone

from ..models import MarketplaceOrder
from .order_listing_format import buyer_from_row, day_range, format_line, fulfillment_from_row, money_str, sum_lines
from .shopify_order_marketplace import SHOPIFY_CHANNEL

MAX_ORDERS_NOT_CREATED = 200

# Pedidos de marketplace que no llegaron a Shopify y merecen una alerta:
# - error al crear la orden (SKU no encontrado, kit vacío, rechazo de Shopify);
# - `error_creando_cliente`, obsoleto pero con filas históricas;
# - `procesando`: el proceso murió a mitad, se revisa a mano;
# - `pending` con error (fallo de la API del canal, pack de Mercado Libre
#   incompleto). Un `pending` sin error es un pedido recién llegado.
NOT_CREATED = Q(shopify_order_id="") & (
    Q(
        status__in=[
            MarketplaceOrder.Status.ERROR_ORDER,
            MarketplaceOrder.Status.ERROR_CUSTOMER,
            MarketplaceOrder.Status.PROCESSING,
        ]
    )
    | (Q(status=MarketplaceOrder.Status.PENDING) & ~Q(error_description=""))
)


def list_orders_not_created(*, marketplace=None, date_from=None, date_to=None, search="", limit=MAX_ORDERS_NOT_CREATED):
    """Pedidos de marketplace sin orden en Shopify, con el error guardado,
    más reciente primero. Solo base de datos.

    Mismos filtros que `list_orders.filter_orders`; las fechas son de llegada al
    sistema (`created_at`, días de Colombia) y `search` es el número del
    pedido en el marketplace. `marketplace="shopify"` → lista vacía.

    Devuelve {"orders_not_created": [...] (hasta `limit`),
    "orders_not_created_count": total real}.
    """
    if marketplace == SHOPIFY_CHANNEL:
        return {"orders_not_created": [], "orders_not_created_count": 0}

    rows = MarketplaceOrder.objects.filter(NOT_CREATED)
    if marketplace:
        rows = rows.filter(marketplace=marketplace)
    start, end = day_range(date_from, date_to)
    if start:
        rows = rows.filter(created_at__gte=start)
    if end:
        rows = rows.filter(created_at__lt=end)
    search = (search or "").strip().lstrip("#")
    if search:
        rows = rows.filter(marketplace_order_number=search)

    page = rows.prefetch_related("items").order_by("-created_at", "-id")[:limit]
    return {
        "orders_not_created": [_format_row(row) for row in page],
        "orders_not_created_count": rows.count(),
    }


def _format_row(row):
    lines, totals = [], []
    for item in row.items.all():
        line, line_total = format_line({"sku": item.marketplace_sku}, item.unit_price, item.quantity)
        lines.append(line)
        totals.append(line_total)
    return {
        "id": row.id,
        "marketplace": row.marketplace,
        "marketplace_order_number": row.marketplace_order_number or row.marketplace_order_id,
        "shipment_id": row.shipment_id,
        "created_at": timezone.localtime(row.created_at).isoformat(),
        "updated_at": timezone.localtime(row.updated_at).isoformat(),
        "status": row.status,
        "error": row.error_description,
        "customer": buyer_from_row(row),
        "fulfillment": fulfillment_from_row(row),
        "items": lines,
        "total": money_str(sum_lines(totals)),
    }
