from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from ..models import Dispatch, MarketplaceOrder, ShopifyOrder
from .order_listing_format import buyer_from_row, day_range, fulfillment_from_row, money_str


def filter_orders(*, marketplace=None, date_from=None, date_to=None, search=""):
    """Queryset de la copia local de pedidos de Shopify (sin borrados), más
    reciente primero. Solo base de datos: nunca consulta Shopify.

    - `marketplace`: valor de `Marketplace` o `shopify`.
    - Fechas: días de Colombia, inclusive, sobre la creación en Shopify.
    - `search`: nombre del pedido en Shopify (sin `#`) o número del pedido
      en el marketplace (`MarketplaceOrder.marketplace_order_number`).
    """
    orders = ShopifyOrder.objects.filter(deleted_at__isnull=True)
    if marketplace:
        orders = orders.filter(marketplace=marketplace)
    start, end = day_range(date_from, date_to)
    if start:
        orders = orders.filter(shopify_created_at__gte=start)
    if end:
        orders = orders.filter(shopify_created_at__lt=end)
    search = (search or "").strip().lstrip("#")
    if search:
        marketplace_ids = (
            MarketplaceOrder.objects.filter(marketplace_order_number=search)
            .exclude(shopify_order_id="")
            .values("shopify_order_id")
        )
        orders = orders.filter(Q(name=search) | Q(shopify_id__in=marketplace_ids))
    return (
        orders.select_related("dispatch__location")
        .prefetch_related("lines", "dispatch__notifications")
        .order_by("-shopify_created_at", "-id")
    )


def format_orders(orders):
    """Forma de respuesta de una página de `ShopifyOrder`, con el comprador
    real, los números del marketplace y la bodega de `MarketplaceOrder`
    (unido por `shopify_order_id`, una sola consulta por página): en Shopify
    los pedidos de marketplace van a nombre del cliente fijo del canal."""
    rows_by_order = defaultdict(list)
    ids = [order.shopify_id for order in orders]
    for row in MarketplaceOrder.objects.filter(shopify_order_id__in=ids).order_by("id"):
        rows_by_order[row.shopify_order_id].append(row)
    return [_format_order(order, rows_by_order.get(order.shopify_id, [])) for order in orders]


def _format_order(order, rows):
    return {
        "shopify_order_id": order.shopify_id,
        "shopify_order_name": order.name,
        "created_at": _iso(order.shopify_created_at),
        "cancelled_at": _iso(order.cancelled_at),
        "financial_status": order.financial_status,
        "fulfillment_status": order.fulfillment_status,
        # La fila local manda; si no hay, el marketplace guardado desde las
        # etiquetas.
        "marketplace": rows[0].marketplace if rows else order.marketplace,
        "marketplace_order_numbers": sorted({row.marketplace_order_number or row.marketplace_order_id for row in rows}),
        "customer": buyer_from_row(rows[0]) if rows else _shopify_customer(order),
        # Todas las filas de un envío comparten la bodega (process_shipment).
        # Sin fila local (tienda web), la del despacho (`assign_web_dispatch`).
        "fulfillment": fulfillment_from_row(rows[0]) if rows else _fulfillment_from_dispatch(order),
        "items": [
            {
                "sku": line.sku,
                "name": line.name,
                "quantity": line.quantity,
                "unit_price": money_str(line.unit_price),
                "line_total": money_str(line.line_total),
            }
            for line in order.lines.all()
        ],
        "total": money_str(order.total),
        "currency": order.currency,
        "dispatch": format_dispatch(order),
    }


def _fulfillment_from_dispatch(order):
    """Bodega de un pedido sin `MarketplaceOrder` (tienda web), con la misma
    forma que `fulfillment_from_row`: asignada si el despacho tiene bodega;
    novedad con el motivo si no (sin stock, SKU desconocido)."""
    try:
        dispatch = order.dispatch
    except Dispatch.DoesNotExist:
        return fulfillment_from_row(None)
    if dispatch.location:
        return {
            "status": "asignada",
            "location_id": dispatch.location.shopify_location_id,
            "location_name": dispatch.location.name,
            "note": "",
        }
    if dispatch.status == Dispatch.Status.CANCELLED:
        return fulfillment_from_row(None)
    return {"status": "novedad", "location_id": "", "location_name": "", "note": dispatch.note}


def format_dispatch(order):
    """Despacho a bodega (`Dispatch`), o None si el proceso aún no lo creó
    (pedidos anteriores a `DISPATCH_START_DATE` nunca lo tienen)."""
    try:
        dispatch = order.dispatch
    except Dispatch.DoesNotExist:
        return None
    return {
        "status": dispatch.status,
        "location_name": dispatch.location.name if dispatch.location else "",
        "location_creates_own_label": bool(dispatch.location and dispatch.location.creates_own_label),
        "note": dispatch.note,
        "tracking_number": dispatch.tracking_number,
        "label_source": dispatch.label_source,
        "label_carrier": dispatch.label_carrier,
        "label_service": dispatch.label_service,
        "notifications": [
            {
                "channel": notification.channel,
                "status": notification.status,
                "recipient": notification.recipient,
                "external_id": notification.external_id,
                "sent_at": _iso(notification.sent_at),
                "error": notification.error_description,
            }
            for notification in dispatch.notifications.all()
        ],
        "updated_at": _iso(dispatch.updated_at),
    }


def _shopify_customer(order):
    return {
        "identification_type": "",
        "identification": order.customer_identification,
        "first_name": order.customer_first_name,
        "last_name": order.customer_last_name,
        "email": order.email,
        "phone": order.phone,
        "city": order.customer_city,
        "region": order.customer_region,
        "address": order.customer_address,
    }


def _iso(value):
    return timezone.localtime(value).isoformat() if value else None
