import hashlib
from datetime import date

from django.db.models import Q
from django.utils import timezone

from config.constants import DISPATCH_NOTIFICATIONS_ENABLED, DISPATCH_START_DATE

from ..models import Dispatch, DispatchNotification, ShopifyOrder
from integrations.envia.functions.download_label import download_label

from .assign_dispatch_location import assign_dispatch_location
from .dispatch_details import dispatch_details
from .fetch_dispatch_label import fetch_dispatch_label
from .notify_dispatch import notify
from .order_listing_format import day_range

# Despachos que una corrida vuelve a intentar.
OPEN_STATUSES = (Dispatch.Status.WAITING_LABEL, Dispatch.Status.READY, Dispatch.Status.ERROR)
RETRYABLE_NOTIFICATIONS = (DispatchNotification.Status.PENDING, DispatchNotification.Status.ERROR)


def dispatch_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso `orders.dispatch_orders`: lleva cada pedido de Shopify a su
    bodega. Ver docs/implementations-plans/order-dispatch-to-warehouses.md.

    Por pedido (desde `DISPATCH_START_DATE`, sin despachar ni borrar):
    1. Crea el `Dispatch` con la bodega (`assign_dispatch_location`); sin
       bodega o sin canales de aviso → `manual`.
    2. Si la bodega exige guía, la busca (`fetch_dispatch_label`); si aún
       no hay → `esperando_guia`, se reintenta en la próxima corrida.
    3. Con `DISPATCH_NOTIFICATIONS_ENABLED`, avisa por cada canal activo de
       la bodega; sin él queda `listo`. Cada aviso se reclama antes de
       enviarse y nunca sale dos veces.

    `params` opcional: `{"limit": N}` y `{"shopify_order_ids": [...]}` para
    una corrida acotada (piloto). Devuelve un conteo por estado final.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    orders = _candidates(params)
    progress_callback(5, f"{len(orders)} pedidos por despachar")

    summary = {}
    total = len(orders) or 1
    for index, order in enumerate(orders):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        status = process_order_dispatch(order)
        summary[status] = summary.get(status, 0) + 1
        progress_callback(5 + int(95 * (index + 1) / total), f"Pedido {order.name}: {status}")
    return summary


def process_order_dispatch(order, *, manual=False):
    """Avanza el despacho de un pedido lo que se pueda. Devuelve su estado.

    `manual=True` es el botón "Notificar a proveedor" del panel: una persona
    lo pide para un pedido, así que trae la guía y avisa aunque
    `DISPATCH_FETCH_LABELS_ENABLED` / `DISPATCH_NOTIFICATIONS_ENABLED` estén
    apagados (esos interruptores son de la automatización). Las escrituras
    en Envía siguen sujetas a sus propios interruptores.
    """
    dispatch = Dispatch.objects.filter(shopify_order=order).select_related("location").first()

    if order.cancelled_at:
        # Uno ya avisado no se toca: la bodega ya lo recibió (avisarle la
        # cancelación es parte del tracking, fase posterior).
        if dispatch is None:
            dispatch = Dispatch.objects.create(shopify_order=order, channel=order.marketplace, status=Dispatch.Status.CANCELLED)
        elif dispatch.status != Dispatch.Status.NOTIFIED:
            _save(dispatch, Dispatch.Status.CANCELLED, "Pedido cancelado en Shopify.")
        return dispatch.status

    if dispatch is None:
        dispatch = _create(order)
    if dispatch.status not in OPEN_STATUSES:
        return dispatch.status

    location = dispatch.location
    if not location.channels():
        return _save(dispatch, Dispatch.Status.MANUAL, f"{location.name} no tiene canales de aviso configurados.")

    label = None
    if dispatch.label_source == Dispatch.LabelSource.ENVIA and dispatch.label_url:
        # Guía generada en Envía: se vuelve a bajar, nunca se genera otra.
        label = {"pdf": download_label(dispatch.label_url), "tracking_number": dispatch.tracking_number}
    elif location.requires_label:
        label, reason = fetch_dispatch_label(order, force=manual)
        if label is None:
            return _save(dispatch, Dispatch.Status.WAITING_LABEL, reason)
        dispatch.tracking_number = label["tracking_number"]
        dispatch.label_source = Dispatch.LabelSource.CHANNEL
        dispatch.label_sha256 = hashlib.sha256(label["pdf"]).hexdigest()
        dispatch.label_fetched_at = timezone.now()

    if not (DISPATCH_NOTIFICATIONS_ENABLED or manual):
        return _save(dispatch, Dispatch.Status.READY, "Avisos apagados (DISPATCH_NOTIFICATIONS_ENABLED).")

    details = dispatch_details(order)
    results = [_notify_channel(dispatch, channel, details, label) for channel in location.channels()]
    if all(status == DispatchNotification.Status.SENT for status in results):
        return _save(dispatch, Dispatch.Status.NOTIFIED, "")
    return _save(dispatch, Dispatch.Status.ERROR, "Algún aviso no se pudo enviar; ver los avisos del despacho.")


def ensure_dispatch(order):
    """El despacho del pedido, creándolo (con su bodega) si no existe."""
    return Dispatch.objects.filter(shopify_order=order).select_related("location").first() or _create(order)


def _create(order):
    location, reason = assign_dispatch_location(order)
    return Dispatch.objects.create(
        shopify_order=order,
        location=location,
        channel=order.marketplace,
        status=Dispatch.Status.WAITING_LABEL if location else Dispatch.Status.MANUAL,
        note=reason,
    )


def _notify_channel(dispatch, channel, details, label):
    notification, _ = DispatchNotification.objects.get_or_create(dispatch=dispatch, channel=channel)
    # Reclamo atómico: solo una corrida pasa de pendiente/error a procesando.
    claimed = DispatchNotification.objects.filter(
        pk=notification.pk, status__in=RETRYABLE_NOTIFICATIONS
    ).update(status=DispatchNotification.Status.PROCESSING, updated_at=timezone.now())
    if not claimed:
        notification.refresh_from_db(fields=["status"])
        return notification.status

    try:
        result = notify(channel, dispatch=dispatch, details=details, label=label)
    except Exception as error:  # noqa: BLE001 -- queda registrado en el aviso
        DispatchNotification.objects.filter(pk=notification.pk).update(
            status=DispatchNotification.Status.ERROR,
            error_description=f"{type(error).__name__}: {error}"[:2000],
            updated_at=timezone.now(),
        )
        return DispatchNotification.Status.ERROR

    DispatchNotification.objects.filter(pk=notification.pk).update(
        status=DispatchNotification.Status.SENT,
        recipient=result["recipient"][:255],
        external_id=result["external_id"][:128],
        error_description="",
        sent_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return DispatchNotification.Status.SENT


def _candidates(params):
    if not DISPATCH_START_DATE:
        return []
    start, _ = day_range(date.fromisoformat(DISPATCH_START_DATE))
    orders = (
        ShopifyOrder.objects.filter(deleted_at__isnull=True, shopify_created_at__gte=start)
        .exclude(fulfillment_status="FULFILLED")
        .filter(Q(dispatch__isnull=True) | Q(dispatch__status__in=OPEN_STATUSES))
        .prefetch_related("lines")
        .order_by("shopify_created_at", "id")
    )
    if params.get("shopify_order_ids"):
        orders = orders.filter(shopify_id__in=[str(value) for value in params["shopify_order_ids"]])
    if params.get("limit"):
        orders = orders[: int(params["limit"])]
    return list(orders)


def _save(dispatch, status, note):
    dispatch.status = status
    dispatch.note = note
    dispatch.save()
    return status
