import hashlib
from datetime import date

from django.db.models import Q
from django.utils import timezone

from ..models import Dispatch, DispatchNotification, ShopifyOrder
from integrations.envia.functions.download_label import download_label

from .assign_dispatch_location import assign_dispatch_location
from .dispatch_details import dispatch_details
from .fetch_dispatch_label import fetch_dispatch_label
from .notify_dispatch import notify
from .order_listing_format import day_range
from .shopify_order_marketplace import SHOPIFY_CHANNEL

# Despachos que una corrida vuelve a intentar.
OPEN_STATUSES = (Dispatch.Status.WAITING_LABEL, Dispatch.Status.READY, Dispatch.Status.ERROR)
RETRYABLE_NOTIFICATIONS = (DispatchNotification.Status.PENDING, DispatchNotification.Status.ERROR)


def dispatch_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso `orders.dispatch_orders`: lleva cada pedido de Shopify a su
    bodega. Ver docs/apps/orders.md ("Despacho a bodegas"). No tiene
    interruptores: se enciende o pausa con su programación en el
    orquestador.

    Por pedido (sin despachar ni borrar):
    1. Crea el `Dispatch` con la bodega (`assign_dispatch_location`); sin
       bodega o sin canales de aviso → `manual`.
    2. Si hace falta guía, la busca (`fetch_dispatch_label`); si aún no hay
       → `esperando_guia`, se reintenta en la próxima corrida.
    3. Avisa por cada canal activo de la bodega. Cada aviso se reclama
       antes de enviarse y nunca sale dos veces.

    `params`:
    - `since` (obligatorio, "AAAA-MM-DD"): solo pedidos creados desde ese
      día (hora de Colombia). Evita despachar históricos: sin fecha el
      proceso falla en vez de tomar todo.
    - `shopify_order_ids` (lista): corrida acotada a esos pedidos (piloto);
      con ella `since` no hace falta.
    - `limit` (N): máximo de pedidos por corrida.

    Devuelve un conteo por estado final.
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


def process_order_dispatch(order):
    """Avanza el despacho de un pedido lo que se pueda. Devuelve su estado.
    Lo usan el proceso y el botón "Notificar a proveedor" del panel."""
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
        # Guía generada en Envía: la guardada (o se baja), nunca se genera otra.
        label = {"pdf": stored_envia_label(dispatch), "tracking_number": dispatch.tracking_number}
    elif location.creates_own_label and order.marketplace == SHOPIFY_CHANNEL:
        pass  # tienda web: la bodega genera la guía (le llega por la tienda conectada); se avisa sin guía
    elif location.requires_label:
        label, reason = fetch_dispatch_label(order)
        if label is None:
            return _save(dispatch, Dispatch.Status.WAITING_LABEL, reason)
        dispatch.tracking_number = label["tracking_number"]
        dispatch.label_source = Dispatch.LabelSource.CHANNEL
        dispatch.label_sha256 = hashlib.sha256(label["pdf"]).hexdigest()
        dispatch.label_fetched_at = timezone.now()

    details = dispatch_details(order)
    results = [_notify_channel(dispatch, channel, details, label) for channel in location.channels()]
    if all(status == DispatchNotification.Status.SENT for status in results):
        return _save(dispatch, Dispatch.Status.NOTIFIED, "")
    return _save(dispatch, Dispatch.Status.ERROR, "Algún aviso no se pudo enviar; ver los avisos del despacho.")


def stored_envia_label(dispatch):
    """El PDF guardado de una guía de Envía; si falta, lo baja de
    `label_url` y lo guarda."""
    if dispatch.label_pdf:
        return bytes(dispatch.label_pdf)
    pdf = download_label(dispatch.label_url)
    dispatch.label_pdf = pdf
    dispatch.label_sha256 = hashlib.sha256(pdf).hexdigest()
    dispatch.save(update_fields=["label_pdf", "label_sha256", "updated_at"])
    return pdf


def ensure_dispatch(order):
    """El despacho del pedido, creándolo (con su bodega) si no existe."""
    return Dispatch.objects.filter(shopify_order=order).select_related("location").first() or _create(order)


def reopen_manual_dispatch(order):
    """Para los botones del panel (una persona lo pide): un despacho en
    `manual` se vuelve a evaluar, porque lo que lo dejó así pudo haberse
    corregido (bodegas sin sincronizar, canales sin configurar, stock que
    llegó). Recalcula la bodega si no tiene y lo deja `esperando_guia`; si
    el motivo sigue, vuelve a `manual` con el motivo actual. El proceso
    automático no reabre despachos manuales. Devuelve el despacho."""
    dispatch = ensure_dispatch(order)
    if dispatch.status != Dispatch.Status.MANUAL:
        return dispatch
    if dispatch.location is None:
        location, reason = assign_dispatch_location(order)
        if location is None:
            _save(dispatch, Dispatch.Status.MANUAL, reason)
            return dispatch
        dispatch.location = location
    _save(dispatch, Dispatch.Status.WAITING_LABEL, "")
    return dispatch


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
        error_description=result.get("warning", "")[:2000],  # enviado, con algo a revisar a mano
        sent_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return DispatchNotification.Status.SENT


def _candidates(params):
    orders = (
        ShopifyOrder.objects.filter(deleted_at__isnull=True)
        .exclude(fulfillment_status="FULFILLED")
        .filter(Q(dispatch__isnull=True) | Q(dispatch__status__in=OPEN_STATUSES))
        .prefetch_related("lines")
        .order_by("shopify_created_at", "id")
    )
    if params.get("since"):
        try:
            start, _ = day_range(date.fromisoformat(str(params["since"])))
        except ValueError as error:
            raise ValueError(f"since inválido ({params['since']!r}): va como AAAA-MM-DD.") from error
        orders = orders.filter(shopify_created_at__gte=start)
    elif not params.get("shopify_order_ids"):
        raise ValueError('Falta el parámetro "since" (AAAA-MM-DD): desde qué día entran pedidos al despacho.')
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
