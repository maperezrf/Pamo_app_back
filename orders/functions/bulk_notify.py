"""Aviso masivo por bodega (botón "Notificar bodegas" del panel): los
pedidos que esperan aviso, agrupados por la bodega que los despacha, y un
solo correo / WhatsApp por bodega con todos sus pedidos listos. Envía sigue
siendo una orden por pedido. Ver docs/apps/orders.md, "Despacho a bodegas".
"""
from datetime import datetime

from django.utils import timezone
from products.models import Marketplace

from ..models import STORED_LABEL_SOURCES, Dispatch, DispatchLocation, ShopifyOrder
from .assign_dispatch_location import assign_dispatch_location
from .dispatch_details import dispatch_details
from .dispatch_orders import (
    OPEN_STATUSES,
    _notify_channel,
    _save,
    claim_notification,
    dispatch_label,
    ensure_dispatches,
    finish_dispatch,
    needs_label,
    record_notification,
    shopify_location_ids,
)
from .fetch_dispatch_label import fetch_dispatch_label
from .notify_dispatch import notify_list
from .shopify_order_marketplace import SHOPIFY_CHANNEL

Channel = DispatchLocation.Channel
# Pedidos de marketplace sin despacho todavía: solo desde que se crean sin
# envío en Shopify y pasan por este flujo (2026-10-11); los de antes los
# importó Envía por la tienda conectada.
MARKETPLACE_SINCE = timezone.make_aware(datetime(2026, 10, 11))
# Guía del canal que solo se puede traer de su API al avisar (en Mercado
# Libre, traerla la marca impresa): la vista previa no la consulta.
FETCHED_ON_NOTIFY = (Marketplace.MERCADOLIBRE, Marketplace.FALABELLA)


class BulkNotifyError(Exception):
    """El aviso masivo no aplica (bodega desconocida, nada listo); el texto
    es para el equipo."""


def bulk_notify_preview():
    """Pedidos que esperan aviso a la bodega, sin escribir nada ni llamar a
    proveedores. Entran los despachos sin avisar (`esperando_guia`, `listo`,
    `error`) o en gestión manual, y los pedidos de marketplace aún sin
    despacho con bodega elegida en la importación; nunca cancelados,
    borrados ni cumplidos en Shopify.

    Devuelve {"locations": [{id, name, requires_label, creates_own_label,
    channels, ready, blocked}], "unassigned": [...]}: `ready` se avisan con
    el botón de la bodega; `blocked` y `unassigned` (sin bodega) llevan el
    motivo en `reason`.
    """
    groups, unassigned = {}, []
    for order, dispatch, location, reason in _candidates():
        item = {
            "shopify_order_id": order.shopify_id,
            "order_name": order.name,
            "marketplace": order.marketplace,
            "created_at": order.shopify_created_at.isoformat() if order.shopify_created_at else None,
            "dispatch_id": dispatch.pk if dispatch else None,
            "label": "",
            "reason": reason,
        }
        if location is None:
            unassigned.append(item)
            continue
        group = groups.setdefault(location.pk, _group(location))
        if not item["reason"]:
            item["label"], item["reason"] = _label_preview(order, dispatch, location)
        (group["blocked"] if item["reason"] else group["ready"]).append(item)
    return {"locations": sorted(groups.values(), key=lambda group: group["name"]), "unassigned": unassigned}


def notify_location(location_id):
    """Botón de una bodega: vuelve a calcular su grupo (pudo cambiar desde
    la vista previa) y avisa sus pedidos `ready`: trae la guía de cada uno
    como el aviso por pedido (`dispatch_label`), manda **un** correo y
    **un** WhatsApp con todos y, si la bodega avisa por Envía, una orden por
    pedido. Cada pedido conserva sus avisos (`DispatchNotification`, uno
    por canal, reclamado antes de enviar: nunca sale dos veces) y termina
    `notificado` (con la guía en Shopify) o `error`.

    Devuelve {"location", "notified", "failed", "waiting"}: nombres de
    pedidos avisados, con algún aviso en error, y los que se quedaron sin
    guía al traerla (con su motivo)."""
    try:
        location = DispatchLocation.objects.filter(pk=int(location_id)).first()
    except (TypeError, ValueError):
        location = None
    if location is None:
        raise BulkNotifyError("Bodega desconocida.")
    group = next((group for group in bulk_notify_preview()["locations"] if group["id"] == location.pk), None)
    ready = group["ready"] if group else []
    if not ready:
        raise BulkNotifyError(f"{location.name} no tiene pedidos listos para avisar.")

    prepared, waiting = [], []
    for item in ready:
        order = ShopifyOrder.objects.prefetch_related("lines", "fulfillment_orders").get(shopify_id=item["shopify_order_id"])
        dispatch = next((row for row in ensure_dispatches(order) if row.location_id == location.pk), None)
        if dispatch is None or dispatch.status not in OPEN_STATUSES:
            waiting.append({**item, "reason": "El despacho cambió; revisar el pedido."})
            continue
        label, reason = dispatch_label(dispatch)
        if reason:
            _save(dispatch, Dispatch.Status.WAITING_LABEL, reason)
            waiting.append({**item, "reason": reason})
            continue
        prepared.append((dispatch, dispatch_details(dispatch), label))

    statuses = {dispatch.pk: [] for dispatch, _, _ in prepared}
    for channel in location.channels():
        if channel == Channel.API:
            for dispatch, details, label in prepared:
                statuses[dispatch.pk].append(_notify_channel(dispatch, channel, details, label))
            continue
        claimed = []
        for entry in prepared:
            notification, status = claim_notification(entry[0], channel)
            if notification is None:
                statuses[entry[0].pk].append(status)
            else:
                claimed.append((notification.pk, entry))
        if not claimed:
            continue
        try:
            result = notify_list(channel, location, [entry for _, entry in claimed])
        except Exception as error:  # noqa: BLE001 -- queda registrado en cada aviso
            status = record_notification([pk for pk, _ in claimed], error=error)
        else:
            status = record_notification([pk for pk, _ in claimed], result=result)
        for _, entry in claimed:
            statuses[entry[0].pk].append(status)

    notified, failed = [], []
    for dispatch, details, _ in prepared:
        done = finish_dispatch(dispatch, statuses[dispatch.pk]) == Dispatch.Status.NOTIFIED
        (notified if done else failed).append(details["order_name"])
    return {"location": location.name, "notified": notified, "failed": failed, "waiting": waiting}


def _candidates():
    """(pedido, despacho o None, bodega o None, motivo) por cada despacho
    que espera aviso y cada pedido de marketplace aún sin despacho."""
    open_orders = dict(deleted_at__isnull=True, cancelled_at__isnull=True)
    dispatches = (
        Dispatch.objects.filter(
            status__in=(*OPEN_STATUSES, Dispatch.Status.MANUAL),
            **{f"shopify_order__{key}": value for key, value in open_orders.items()},
        )
        .exclude(shopify_order__fulfillment_status="FULFILLED")
        .select_related("location", "shopify_order")
        .prefetch_related("shopify_order__fulfillment_orders")
        .order_by("shopify_order__shopify_created_at", "id")
    )
    for dispatch in dispatches:
        order = dispatch.shopify_order
        reason = (dispatch.note or "Gestión manual.") if dispatch.status == Dispatch.Status.MANUAL else ""
        if dispatch.location is None:
            yield order, dispatch, None, dispatch.note or "Sin bodega asignada."
            continue
        yield order, dispatch, dispatch.location, reason or _shopify_mismatch(order, dispatch, dispatch.location)

    without_dispatch = (
        ShopifyOrder.objects.filter(dispatches__isnull=True, shopify_created_at__gte=MARKETPLACE_SINCE, **open_orders)
        .exclude(marketplace__in=["", SHOPIFY_CHANNEL])
        .exclude(fulfillment_status="FULFILLED")
        .prefetch_related("fulfillment_orders")
        .order_by("shopify_created_at", "id")
    )
    for order in without_dispatch:
        location, reason = assign_dispatch_location(order)
        yield order, None, location, reason or (_shopify_mismatch(order, None, location) if location else "")


def _shopify_mismatch(order, dispatch, location):
    """Nunca se avisa a una bodega distinta de la que tiene Shopify (ver
    `follow_shopify_locations`). Sin fulfillment orders abiertos en la copia
    local no hay con qué comparar y se deja pasar."""
    location_ids = shopify_location_ids(order)
    if not location_ids:
        return ""
    if dispatch is not None and dispatch.split:
        ok = location.shopify_location_id in location_ids
    elif len(location_ids) > 1:
        return "Shopify reparte el pedido en varias bodegas: avisar desde el pedido."
    else:
        ok = location_ids[0] == location.shopify_location_id
    if ok:
        return ""
    names = {item.location_id: item.location_name for item in order.fulfillment_orders.all()}
    return f"Shopify tiene el pedido en otra bodega: {', '.join(names.get(i) or i for i in location_ids)}."


def _label_preview(order, dispatch, location):
    """(texto de la guía, motivo si falta) sin llamar a proveedores: la
    misma decisión de `dispatch_label`."""
    if not location.channels():
        return "", f"{location.name} no tiene canales de aviso configurados."
    if dispatch and dispatch.label_source in STORED_LABEL_SOURCES and (dispatch.label_pdf or dispatch.label_url):
        return f"Guía {dispatch.tracking_number}", ""
    if not needs_label(order, location):
        return ("La bodega crea la guía" if location.creates_own_label else "Sin guía (la bodega no la exige)"), ""
    if order.marketplace in FETCHED_ON_NOTIFY:
        return "Guía del canal (se trae al avisar)", ""
    if order.marketplace == Marketplace.SODIMAC:
        label, reason = fetch_dispatch_label(order)  # la del RPA, guardada en la base
        return (f"Guía {label['tracking_number']}", "") if label else ("", reason)
    return "", "Falta la guía: generarla o cargarla en el pedido."


def _group(location):
    return {
        "id": location.pk,
        "name": location.name,
        "requires_label": location.requires_label,
        "creates_own_label": location.creates_own_label,
        "channels": list(location.channels()),
        "ready": [],
        "blocked": [],
    }
