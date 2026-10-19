import hashlib
from datetime import date

from django.db.models import Q
from django.utils import timezone

from ..models import STORED_LABEL_SOURCES, Dispatch, DispatchLocation, DispatchNotification, ShopifyOrder
from integrations.envia.functions.download_label import download_label

from .assign_dispatch_location import assign_dispatch_location
from .dispatch_details import dispatch_details
from .fetch_dispatch_label import fetch_dispatch_label
from .notify_dispatch import notify
from .order_listing_format import day_range
from .shopify_fulfillment import CLOSED_STATUSES, fulfill_in_shopify
from .shopify_order_marketplace import SHOPIFY_CHANNEL

# Despachos que una corrida vuelve a intentar.
OPEN_STATUSES = (Dispatch.Status.WAITING_LABEL, Dispatch.Status.READY, Dispatch.Status.ERROR)
RETRYABLE_NOTIFICATIONS = (DispatchNotification.Status.PENDING, DispatchNotification.Status.ERROR)
# Estado de un pedido con varios despachos: el primero de esta lista que
# tenga alguno (lo que más atención pide).
STATUS_PRIORITY = (
    Dispatch.Status.ERROR, Dispatch.Status.MANUAL, Dispatch.Status.WAITING_LABEL, Dispatch.Status.READY,
    Dispatch.Status.NOTIFIED, Dispatch.Status.CANCELLED,
)


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
    4. Ya avisado, deja la guía en Shopify (`fulfill_in_shopify`).

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
    """Avanza los despachos de un pedido lo que se pueda (uno por bodega si
    Shopify lo repartió). Devuelve el estado del pedido: el que más atención
    pide entre sus despachos (`STATUS_PRIORITY`)."""
    if order.cancelled_at:
        # Uno ya avisado no se toca: la bodega ya lo recibió (avisarle la
        # cancelación es parte del tracking, fase posterior).
        dispatches = list(Dispatch.objects.filter(shopify_order=order).select_related("location"))
        if not dispatches:
            Dispatch.objects.create(shopify_order=order, channel=order.marketplace, status=Dispatch.Status.CANCELLED)
            return Dispatch.Status.CANCELLED
        for dispatch in dispatches:
            if dispatch.status != Dispatch.Status.NOTIFIED:
                _save(dispatch, Dispatch.Status.CANCELLED, "Pedido cancelado en Shopify.")
        return worst_status(dispatches)

    dispatches = ensure_dispatches(order)
    for dispatch in dispatches:
        process_dispatch(dispatch)
    return worst_status(dispatches)


def worst_status(dispatches):
    statuses = {dispatch.status for dispatch in dispatches}
    return next(status for status in STATUS_PRIORITY if status in statuses)


def process_dispatch(dispatch):
    """Avanza un despacho lo que se pueda. Devuelve su estado. Lo usan el
    proceso y el botón "Notificar a proveedor" del panel."""
    order = dispatch.shopify_order
    if dispatch.status not in OPEN_STATUSES:
        if dispatch.status == Dispatch.Status.NOTIFIED:
            fulfill_in_shopify(dispatch)  # reintento si la guía no quedó en Shopify
        return dispatch.status

    if dispatch.split:
        # Fase 2 de docs/implementations-plans/dispatch-per-fulfillment-order.md:
        # avisar por bodega y cumplir en Shopify solo su fulfillment order.
        return dispatch.status

    location = dispatch.location
    if not location.channels():
        return _save(dispatch, Dispatch.Status.MANUAL, f"{location.name} no tiene canales de aviso configurados.")

    label = None
    if dispatch.label_source in STORED_LABEL_SOURCES and (dispatch.label_pdf or dispatch.label_url):
        # Guía generada en Envía o cargada a mano: la guardada (o se baja), nunca se genera otra.
        label = {"pdf": stored_envia_label(dispatch), "tracking_number": dispatch.tracking_number}
    elif location.creates_own_label and order.marketplace == SHOPIFY_CHANNEL:
        pass  # tienda web: la bodega genera la guía (le llega por la tienda conectada); se avisa sin guía
    elif location.requires_label:
        label, reason = fetch_dispatch_label(order)
        if label is None:
            return _save(dispatch, Dispatch.Status.WAITING_LABEL, reason)
        dispatch.tracking_number = label["tracking_number"]
        dispatch.label_source = Dispatch.LabelSource.CHANNEL
        dispatch.label_pdf = label["pdf"]
        dispatch.label_sha256 = hashlib.sha256(label["pdf"]).hexdigest()
        dispatch.label_fetched_at = timezone.now()

    details = dispatch_details(dispatch)
    results = [_notify_channel(dispatch, channel, details, label) for channel in location.channels()]
    if all(status == DispatchNotification.Status.SENT for status in results):
        _save(dispatch, Dispatch.Status.NOTIFIED, "")
        # Después del aviso, no antes: el proceso salta los pedidos ya
        # cumplidos en Shopify y la bodega se quedaría sin aviso.
        fulfill_in_shopify(dispatch)
        return dispatch.status
    return _save(dispatch, Dispatch.Status.ERROR, "Algún aviso no se pudo enviar; ver los avisos del despacho.")


def stored_envia_label(dispatch):
    """El PDF guardado de una guía de Envía o cargada a mano; si falta (guía
    de Envía anterior a guardar PDFs), lo baja de `label_url` y lo guarda."""
    if dispatch.label_pdf:
        return bytes(dispatch.label_pdf)
    pdf = download_label(dispatch.label_url)
    dispatch.label_pdf = pdf
    dispatch.label_sha256 = hashlib.sha256(pdf).hexdigest()
    dispatch.save(update_fields=["label_pdf", "label_sha256", "updated_at"])
    return pdf


def ensure_dispatches(order):
    """Los despachos del pedido, creándolos si no existen: uno con su bodega
    o, si Shopify repartió un pedido web en varias bodegas, uno por bodega
    (`split_location_ids`).

    Un despacho único que todavía no hizo nada (sin avisos ni guía) se
    reemplaza por los de cada bodega si Shopify ya repartió el pedido (p. ej.
    #20577 quedó `manual`, "ninguna bodega tiene el pedido completo", antes
    de que existieran los despachos por bodega)."""
    dispatches = list(Dispatch.objects.filter(shopify_order=order).select_related("location").order_by("id"))
    for dispatch in dispatches:
        dispatch.shopify_order = order  # el mismo objeto, con sus líneas ya cargadas
    location_ids = split_location_ids(order)
    if len(dispatches) == 1 and location_ids and _replaceable(dispatches[0]):
        dispatches[0].delete()
        dispatches = []
    if dispatches:
        return dispatches
    if location_ids:
        return _create_split(order, location_ids)
    return [_create(order)]


def split_location_ids(order):
    """Bodegas (ids de `Location` de Shopify) de un pedido de la **tienda
    web** que Shopify repartió en más de una: las de sus fulfillment orders
    abiertos, en orden. Vacío si sale de una sola o es de marketplace (por
    ahora un envío de marketplace sale de una sola bodega). Lee la copia
    local, que el webhook de pedidos refresca antes de crear el despacho."""
    if order.marketplace != SHOPIFY_CHANNEL:
        return []
    location_ids = shopify_location_ids(order)
    return location_ids if len(location_ids) > 1 else []


def follow_shopify_locations(order):
    """Alinea los despachos del pedido con las bodegas que tiene en Shopify
    después de que alguien las cambió allá (aviso `fulfillment_orders/moved`,
    `/split` o `/merged`; ver docs/apps/orders.md, "Despacho a bodegas").
    Lee la copia local, que el webhook acaba de refrescar.

    - Sin despachos, sin fulfillment orders abiertos (ya cumplido) o con las
      mismas bodegas: no hace nada. Así un movimiento hecho por
      `fulfill_in_shopify` (bodega ya avisada) no cambia nada.
    - Si ningún despacho hizo nada todavía (sin guía ni avisos): se borran y
      se crean de nuevo en la(s) bodega(s) de Shopify.
    - Si alguno ya tiene guía o avisos: no se reasigna (la guía sale de la
      bodega anterior). Los que no se han avisado pasan a `manual` con el
      motivo, para que nunca se avise a la bodega equivocada; los avisados
      quedan igual, con la nota.

    Devuelve los despachos del pedido.
    """
    dispatches = list(Dispatch.objects.filter(shopify_order=order).select_related("location").order_by("id"))
    location_ids = shopify_location_ids(order)
    current = {dispatch.location.shopify_location_id for dispatch in dispatches if dispatch.location}
    if not dispatches or not location_ids or (
        set(location_ids) == current and all(dispatch.location for dispatch in dispatches)
    ):
        return dispatches

    names = {item.location_id: item.location_name for item in order.fulfillment_orders.all()}
    shopify_names = ", ".join(names.get(location_id) or location_id for location_id in location_ids)
    if all(_untouched(dispatch) for dispatch in dispatches):
        for dispatch in dispatches:
            dispatch.delete()
        if len(location_ids) == 1:
            return [_create_at(order, location_ids[0], names.get(location_ids[0]))]
        if order.marketplace == SHOPIFY_CHANNEL:
            return _create_split(order, location_ids)
        # Marketplace: un envío sale de una sola bodega (decisión E de Mercado Libre).
        return [Dispatch.objects.create(
            shopify_order=order, channel=order.marketplace, status=Dispatch.Status.MANUAL,
            note=f"En Shopify el pedido quedó repartido en {shopify_names}: un envío de marketplace sale de una sola bodega.",
        )]

    note = f"En Shopify la bodega cambió a {shopify_names}, pero el despacho ya tiene guía o avisos: revisar a mano."
    for dispatch in dispatches:
        if dispatch.status == Dispatch.Status.NOTIFIED:
            dispatch.note = note
            dispatch.save(update_fields=["note", "updated_at"])
        elif dispatch.status != Dispatch.Status.CANCELLED:
            _save(dispatch, Dispatch.Status.MANUAL, note)
    return dispatches


def shopify_location_ids(order):
    """Bodegas (ids de `Location` de Shopify) de los fulfillment orders
    abiertos del pedido, en orden y sin repetir, según la copia local."""
    location_ids = []
    for item in order.fulfillment_orders.all():
        if item.status not in CLOSED_STATUSES and item.location_id and item.location_id not in location_ids:
            location_ids.append(item.location_id)
    return location_ids


def reopen_manual_dispatch(dispatch):
    """Para los botones del panel (una persona lo pide): un despacho en
    `manual` se vuelve a evaluar, porque lo que lo dejó así pudo haberse
    corregido (bodegas sin sincronizar, canales sin configurar, stock que
    llegó). Recalcula la bodega si no tiene y lo deja `esperando_guia`; si
    el motivo sigue, vuelve a `manual` con el motivo actual. El proceso
    automático no reabre despachos manuales. Devuelve el despacho."""
    if dispatch.status != Dispatch.Status.MANUAL:
        return dispatch
    if dispatch.location is None:
        if dispatch.split:
            return dispatch  # bodega de Shopify sin registrar: se resuelve sincronizando bodegas
        location, reason = assign_dispatch_location(dispatch.shopify_order)
        if location is None:
            _save(dispatch, Dispatch.Status.MANUAL, reason)
            return dispatch
        dispatch.location = location
    _save(dispatch, Dispatch.Status.WAITING_LABEL, "")
    return dispatch


def _replaceable(dispatch):
    return not dispatch.split and _untouched(dispatch)


def _create(order):
    location, reason = assign_dispatch_location(order)
    return Dispatch.objects.create(
        shopify_order=order,
        location=location,
        channel=order.marketplace,
        status=Dispatch.Status.WAITING_LABEL if location else Dispatch.Status.MANUAL,
        note=reason,
    )


def _untouched(dispatch):
    """El despacho no hizo nada todavía: se puede borrar y crear de nuevo."""
    return (
        dispatch.status in (*OPEN_STATUSES, Dispatch.Status.MANUAL)
        and not dispatch.tracking_number
        and not dispatch.notifications.exists()
    )


def _create_at(order, location_id, location_name=""):
    """Despacho en la bodega que Shopify le asignó al pedido."""
    location = DispatchLocation.objects.filter(shopify_location_id=location_id).first()
    return Dispatch.objects.create(
        shopify_order=order,
        location=location,
        channel=order.marketplace,
        status=Dispatch.Status.WAITING_LABEL if location else Dispatch.Status.MANUAL,
        note="" if location else f"Bodega de Shopify sin registrar: {location_name or location_id} (sincronizar bodegas).",
    )


def _create_split(order, location_ids):
    locations = {
        item.shopify_location_id: item for item in DispatchLocation.objects.filter(shopify_location_id__in=location_ids)
    }
    dispatches = [
        Dispatch.objects.create(
            shopify_order=order, location=locations[location_id], channel=order.marketplace, split=True,
            status=Dispatch.Status.WAITING_LABEL,
        )
        for location_id in location_ids
        if location_id in locations
    ]
    names = {item.location_id: item.location_name for item in order.fulfillment_orders.all()}
    missing = [names.get(location_id) or location_id for location_id in location_ids if location_id not in locations]
    if missing:
        # Uno solo sin bodega por pedido (restricción del modelo), con todas las que faltan.
        dispatches.append(
            Dispatch.objects.create(
                shopify_order=order, channel=order.marketplace, split=True, status=Dispatch.Status.MANUAL,
                note=f"Bodega de Shopify sin registrar: {', '.join(missing)} (sincronizar bodegas).",
            )
        )
    return dispatches


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
        .filter(Q(dispatches__isnull=True) | Q(dispatches__status__in=OPEN_STATUSES))
        .distinct()
        .prefetch_related("lines", "fulfillment_orders")
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
