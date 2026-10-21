from collections import defaultdict

from django.db.models import BooleanField, ExpressionWrapper, Prefetch, Q
from django.utils import timezone

from ..models import Dispatch, MarketplaceLabel, MarketplaceOrder, ShopifyOrder
from .dispatch_details import dispatch_lines
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
    return orders.prefetch_related(
        "lines", Prefetch("dispatches", queryset=_dispatches()), "fulfillment_orders__lines"
    ).order_by("-shopify_created_at", "-id")


def _dispatches():
    """Despachos para el listado, sin traer el PDF de la guía."""
    return (
        Dispatch.objects.select_related("location").defer("label_pdf").prefetch_related("notifications").order_by("id")
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
    labels = {label.order_id: label for label in _labels(order__shopify_order_id__in=ids)}
    return [_format_order(order, rows_by_order.get(order.shopify_id, []), labels) for order in orders]


def _format_order(order, rows, labels):
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
        "dispatches": [format_dispatch(dispatch) for dispatch in order.dispatches.all()],
        "fulfillment_orders": [_format_fulfillment_order(item) for item in order.fulfillment_orders.all()],
        "channel_label": _channel_label(rows[0], labels.get(rows[0].pk)) if rows else None,
    }


def channel_label_for_order(order):
    """`channel_label` de un pedido suelto (respuesta de los botones)."""
    row = MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id").first()
    if row is None:
        return None
    return _channel_label(row, _labels(order=row).first())


def _labels(**filters):
    """Guías cargadas, sin traer los PDF (`only` los deja diferidos), con
    `has_relation` (la guía Home Delivery no trae relación)."""
    return (
        MarketplaceLabel.objects.filter(**filters)
        .only("order_id", "tracking_number", "uploaded_at")
        .annotate(has_relation=ExpressionWrapper(~Q(relation_pdf=b""), output_field=BooleanField()))
    )


def _channel_label(row, label):
    """Guía que genera un tercero (hoy el RPA de Sodimac), o None en los
    canales que no la usan. `status`: `pendiente` (no se ha pedido),
    `solicitada`, `recibida`, `error` (se pide otra vez) o `manual`
    (revisión manual, el motivo en `note`). `result`: qué pasó en la última
    corrida del RPA (`MarketplaceOrder.LabelRequestResult`, "" si no hubo o
    la guía se cargó a mano). Los PDF se bajan por
    `GET /api/orders/<id>/channel-label/<rotulo|relacion>/`; `has_relation`
    es falso en la guía Home Delivery."""
    if row.marketplace != MarketplaceOrder.Marketplace.SODIMAC:
        return None
    return {
        "status": MarketplaceOrder.LabelRequestStatus.RECEIVED if label else (row.label_request_status or "pendiente"),
        "note": "" if label else row.label_request_note,
        "result": row.label_request_result,
        "result_label": row.get_label_request_result_display() if row.label_request_result else "",
        "tracking_number": label.tracking_number if label else "",
        "has_relation": bool(label and label.has_relation),
        "received_at": _iso(label.uploaded_at) if label else None,
        "requested_at": _iso(row.label_requested_at),
    }


def _format_fulfillment_order(item):
    """Una bodega desde donde sale el pedido según Shopify
    (`ShopifyFulfillmentOrder`), con la forma de `normalize_order`."""
    return {
        "id": item.shopify_id,
        "status": item.status,
        "location_id": item.location_id,
        "location_name": item.location_name,
        "items": [{"sku": line.sku, "name": line.name, "quantity": line.quantity} for line in item.lines.all()],
        "tracking": {"number": item.tracking_number, "company": item.tracking_company, "url": item.tracking_url},
    }


def _fulfillment_from_dispatch(order):
    """Bodega de un pedido sin `MarketplaceOrder` (tienda web), con la misma
    forma que `fulfillment_from_row`: asignada si el despacho tiene bodega;
    novedad con el motivo si no (sin stock, SKU desconocido). Repartido en
    varias bodegas: asignada con los nombres separados por coma."""
    dispatches = list(order.dispatches.all())
    if not dispatches:
        return fulfillment_from_row(None)
    if len(dispatches) > 1:
        located = [dispatch.location for dispatch in dispatches if dispatch.location]
        return {
            "status": "asignada",
            "location_id": ",".join(location.shopify_location_id for location in located),
            "location_name": ", ".join(location.name for location in located),
            "note": " ".join(dispatch.note for dispatch in dispatches if not dispatch.location),
        }
    dispatch = dispatches[0]
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


def format_dispatches(order):
    """Todos los despachos del pedido (uno por bodega si Shopify lo
    repartió); `[]` si el proceso aún no los creó (pedidos anteriores al
    `since` del proceso nunca los tienen)."""
    return [format_dispatch(dispatch) for dispatch in _dispatches().filter(shopify_order=order)]


def format_dispatch(dispatch):
    """Despacho a bodega (`Dispatch`), con los productos que lleva."""
    return {
        "id": dispatch.pk,
        "split": dispatch.split,
        "status": dispatch.status,
        "location_id": dispatch.location.shopify_location_id if dispatch.location else "",
        "location_name": dispatch.location.name if dispatch.location else "",
        "location_creates_own_label": bool(dispatch.location and dispatch.location.creates_own_label),
        "note": dispatch.note,
        "tracking_number": dispatch.tracking_number,
        "label_source": dispatch.label_source,
        "label_carrier": dispatch.label_carrier,
        "label_service": dispatch.label_service,
        "label_package": _label_packages(dispatch),
        "shopify_fulfilled_at": _iso(dispatch.shopify_fulfilled_at),
        "shopify_fulfillment_error": dispatch.shopify_fulfillment_error,
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
        "items": [
            {"sku": line["sku"], "name": line["name"], "quantity": line["quantity"]} for line in dispatch_lines(dispatch)
        ],
        "updated_at": _iso(dispatch.updated_at),
    }


def _label_packages(dispatch):
    """`label_package` como lista de filas. Las guías generadas antes del
    2026-10-19 guardaban un solo paquete (`{"weight", ...}`, sin cantidad)."""
    packages = dispatch.label_package
    if isinstance(packages, dict):
        return [{**packages, "quantity": 1}] if packages else []
    return packages or []


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
