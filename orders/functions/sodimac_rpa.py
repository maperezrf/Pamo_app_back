import base64
import binascii
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Prefetch, Q
from django.utils import timezone

from config.constants import SODIMAC_RPA_ALERT_EMAILS, SODIMAC_RPA_URL
from integrations.sodimac_rpa.client import SodimacRpaBusy, SodimacRpaError
from integrations.sodimac_rpa.functions.run_control import continue_run, get_status, kill_run, viewer_url
from integrations.sodimac_rpa.functions.trigger_labels import trigger_labels

from ..models import Dispatch, MarketplaceLabel, MarketplaceOrder, ShopifyOrder, SodimacRpaRun, SodimacRpaRunItem
from .assign_dispatch_location import ASSIGNED_STATUSES
from .dispatch_orders import shopify_location_ids
from .read_label_tracking import read_label_tracking

logger = logging.getLogger(__name__)

SODIMAC = MarketplaceOrder.Marketplace.SODIMAC
FINAL_STATUS = "4-ESTADO FINAL"
Request = MarketplaceOrder.LabelRequestStatus
Result = MarketplaceOrder.LabelRequestResult
Run = SodimacRpaRun.Status
# Una solicitud sin respuesta después de esto se da por perdida (el RPA se
# reinició, falló el webhook...) y se vuelve a pedir.
STALE_REQUEST = timedelta(hours=6)
MAX_PDF_BYTES = 5 * 1024 * 1024
# Sodimac no deja despachar una OC desde dos bodegas: con el pedido repartido
# en Shopify no se pide guía (ni en lote ni con el botón del pedido).
SPLIT_IN_SHOPIFY = "Shopify reparte el pedido en varias bodegas y Sodimac no permite despachar desde dos."
# Ubicaciones de Shopify que no son una bodega de despacho válida para el RPA
# (decisión del usuario, 2026-10-26): un pedido asignado ahí es novedad.
INVALID_LOCATIONS = {"proveedores"}
# El lote ("Generar guías disponibles") solo muestra las OC de este día en
# adelante (fecha de la OC en Sodimac): las anteriores las gestionó
# operaciones por otro lado (decisión del usuario, 2026-10-27). El botón de
# cada pedido no tiene este corte.
BULK_LABELS_SINCE = timezone.make_aware(datetime(2026, 10, 27))


def _invalid_location(name):
    return (name or "").strip().lower() in INVALID_LOCATIONS


class RpaEventError(Exception):
    """El evento del RPA no se puede procesar (payload inválido)."""


def _requestable():
    """Solicitud que se puede (re)enviar: nunca pedida, fallida o perdida
    (`STALE_REQUEST`). Las de revisión manual no."""
    return Q(label_request_status__in=["", Request.ERROR]) | Q(
        label_request_status=Request.REQUESTED, label_requested_at__lt=timezone.now() - STALE_REQUEST
    )


def request_sodimac_labels(purchase_orders=None, *, source=None, requested_by=None):
    """Pide al RPA la guía de las OC de Sodimac que la necesitan.

    - Sin `purchase_orders` (lo llama `orders.sync_sodimac` al terminar de
      crear las órdenes en Shopify): las OC con orden en Shopify, bodega
      asignada, sin guía y abiertas en Sodimac, cuya solicitud no exista,
      haya fallado o se haya perdido (`STALE_REQUEST`). Las de revisión
      manual no. Sin `SODIMAC_RPA_URL` no hace nada: el RPA todavía no está
      conectado.
    - Con `purchase_orders` (acción del admin, una persona las elige):
      esas OC, si tienen orden en Shopify, bodega y no tienen guía, en
      cualquier estado de solicitud.

    Devuelve las OC enviadas al RPA. Si el RPA ya está corriendo no se
    marca nada: se piden en la próxima corrida.

    Cada envío queda en el historial (`SodimacRpaRun`) con `source` (por
    defecto, sync sin `purchase_orders` y admin con ellas) y
    `requested_by`.
    """
    candidates = (
        MarketplaceOrder.objects.filter(
            marketplace=SODIMAC,
            status=MarketplaceOrder.Status.CREATED,
            fulfillment_status__in=ASSIGNED_STATUSES,
            label__isnull=True,
        )
        .exclude(shopify_order_id="")
        .order_by("created_at")
    )
    if purchase_orders is not None:
        candidates = candidates.filter(marketplace_order_id__in=[str(oc) for oc in purchase_orders])
    else:
        if not SODIMAC_RPA_URL:
            return []
        candidates = candidates.exclude(marketplace_status=FINAL_STATUS).filter(_requestable())
    rows = list(candidates)
    pending = [row.marketplace_order_id for row in rows]
    if not pending:
        return []
    if source is None:
        source = SodimacRpaRun.Source.SYNC if purchase_orders is None else SodimacRpaRun.Source.ADMIN
    # Antes del /trigger: el RPA puede responder por la primera OC antes de
    # que esta función termine.
    run = _open_run(rows, source, requested_by)
    try:
        trigger_labels(pending)
    except SodimacRpaBusy:
        run.delete()
        if purchase_orders is not None:
            raise
        logger.info("RPA de Sodimac ocupado; %s OC quedan para la próxima corrida", len(pending))
        return []
    except Exception:
        run.delete()
        raise
    _interrupt_older_runs(run)
    MarketplaceOrder.objects.filter(marketplace=SODIMAC, marketplace_order_id__in=pending).update(
        label_request_status=Request.REQUESTED,
        label_request_note="",
        label_request_result="",
        label_requested_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return pending


def _open_run(rows, source, requested_by):
    run = SodimacRpaRun.objects.create(
        source=source, requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None
    )
    SodimacRpaRunItem.objects.bulk_create(
        [SodimacRpaRunItem(run=run, order=row, purchase_order=row.marketplace_order_id) for row in rows]
    )
    return run


def _interrupt_older_runs(run):
    """El RPA corre un lote a la vez: si una corrida anterior sigue abierta,
    se cortó sin avisar el fin del lote. Sus OC sin respuesta quedan como
    `sin_respuesta` (la OC ya se volvió a pedir en esta)."""
    older = SodimacRpaRun.objects.filter(status=Run.RUNNING, pk__lt=run.pk)
    SodimacRpaRunItem.objects.filter(run__in=older, status=Request.REQUESTED).update(
        status=Request.ERROR, result=Result.NO_RESPONSE, note="La corrida se cortó sin avisar el fin del lote.",
        updated_at=timezone.now(),
    )
    older.update(status=Run.INTERRUPTED, finished_at=timezone.now())


def _log_item(order, status, note, result, tracking=""):
    """Anota en el historial lo que pasó con la OC en la corrida abierta que
    la incluye (la más reciente). Sin corrida abierta no hay dónde."""
    item = SodimacRpaRunItem.objects.filter(order=order, run__status=Run.RUNNING).order_by("-run_id").first()
    if item is None:
        return
    item.status, item.note, item.result, item.tracking_number = status, note, result or "", tracking
    item.save(update_fields=["status", "note", "result", "tracking_number", "updated_at"])


class LabelRequestError(Exception):
    """La guía no se puede pedir al RPA para este pedido; el texto es para
    el equipo (se muestra en el panel)."""


def request_label_for_order(order, requested_by=None):
    """Botón "Generar guía (RPA)" del panel: pide al RPA la guía de la OC de
    Sodimac del pedido de Shopify `order`, sin esperar al sync. Una persona
    lo pide, así que se manda aunque la solicitud anterior esté en error o
    en revisión manual; nunca si la bodega no es válida
    (`INVALID_LOCATIONS`) o Shopify reparte el pedido en varias bodegas
    (`SPLIT_IN_SHOPIFY`). `SodimacRpaBusy` / `SodimacRpaError` se propagan."""
    row = (
        MarketplaceOrder.objects.filter(marketplace=SODIMAC, shopify_order_id=order.shopify_id)
        .order_by("id")
        .first()
    )
    if row is None:
        raise LabelRequestError("El pedido no es una OC de Sodimac creada por este sistema.")
    if MarketplaceLabel.objects.filter(order=row).exists():
        raise LabelRequestError("La OC ya tiene guía: descargarla en el recuadro de la guía.")
    if row.fulfillment_status not in ASSIGNED_STATUSES or not row.fulfillment_location_name:
        raise LabelRequestError(f"La OC no tiene bodega: {row.fulfillment_note or 'sin asignar'}.")
    if _invalid_location(row.fulfillment_location_name):
        raise LabelRequestError(f"La bodega {row.fulfillment_location_name} no es válida para generar guía.")
    if len(shopify_location_ids(order)) > 1:
        raise LabelRequestError(SPLIT_IN_SHOPIFY)
    if not request_sodimac_labels(
        [row.marketplace_order_id], source=SodimacRpaRun.Source.ORDER, requested_by=requested_by
    ):
        raise LabelRequestError("La OC no se pudo pedir al RPA (revisar que tenga orden en Shopify).")
    row.refresh_from_db()
    return row


def bulk_label_preview():
    """Botón "Generar guías disponibles": todas las OC de Sodimac que no
    tienen guía todavía (orden en Shopify, abiertas en Sodimac), en
    cualquier estado de solicitud: una que el RPA no pudo generar sigue a
    la vista hasta tener guía (decisión del usuario, 2026-10-26). Separadas
    en:

    - `ready`: la bodega de la plataforma (`fulfillment_location_id`, la que
      el RPA usa en el portal) es la única bodega abierta del pedido en
      Shopify (copia local de sus fulfillment orders).
      Una en revisión manual (el RPA no pudo) también va aquí, para
      reintentarla, con lo que dijo el RPA en `note`; igual una pedida que
      el RPA dejó sin respuesta (ya no está corriendo: se cortó sin avisar
      el fin del lote).
    - `blocked`: no coinciden, o falta alguna de las dos; o pedida y sin
      respuesta mientras el RPA corre o no responde (para no mandarla dos
      veces).

    Los pedidos ya cumplidos en Shopify no aparecen: su guía salió por otro
    lado. Tampoco las OC anteriores a `BULK_LABELS_SINCE` (sin fecha de la
    OC, cuenta la de importación).
      `reason` dice por qué. No se mandan al RPA en el lote: una guía desde
      la bodega equivocada no se puede usar.
    """
    rows = list(
        MarketplaceOrder.objects.filter(
            marketplace=SODIMAC, status=MarketplaceOrder.Status.CREATED, label__isnull=True
        )
        .exclude(shopify_order_id="")
        .exclude(marketplace_status=FINAL_STATUS)
        .filter(
            Q(marketplace_created_at__gte=BULK_LABELS_SINCE)
            | Q(marketplace_created_at__isnull=True, created_at__gte=BULK_LABELS_SINCE)
        )
        .order_by("created_at")
    )
    rpa_idle = _rpa_idle()
    requestable = set(
        MarketplaceOrder.objects.filter(pk__in=[row.pk for row in rows]).filter(_requestable()).values_list("pk", flat=True)
    )
    orders = {
        order.shopify_id: order
        for order in ShopifyOrder.objects.filter(shopify_id__in=[row.shopify_order_id for row in rows]).prefetch_related(
            "fulfillment_orders"
        )
    }
    ready, blocked = [], []
    for row in rows:
        order = orders.get(row.shopify_order_id)
        if order is not None and order.fulfillment_status == "FULFILLED":
            continue
        lost = row.label_request_status == Request.REQUESTED and rpa_idle
        location_ids = shopify_location_ids(order) if order else []
        names = {item.location_id: item.location_name for item in order.fulfillment_orders.all()} if order else {}
        item = {
            "shopify_order_id": row.shopify_order_id,
            "order_name": order.name if order else "",
            "purchase_order": row.marketplace_order_id,
            "platform_location": row.fulfillment_location_name if row.fulfillment_status in ASSIGNED_STATUSES else "",
            "shopify_location": ", ".join(names.get(location_id) or location_id for location_id in location_ids),
            "reason": _location_mismatch(row, order, location_ids)
            or ("" if row.pk in requestable or lost or row.label_request_status == Request.MANUAL else _WAITING_RPA),
            "note": "El RPA terminó sin responder por esta OC." if lost else _manual_note(row),
        }
        (blocked if item["reason"] else ready).append(item)
    return {"ready": ready, "blocked": blocked}


_WAITING_RPA = "Pedida al RPA, esperando respuesta."


def _rpa_idle():
    """El RPA responde y no tiene corrida: una OC que sigue `solicitada` ya
    no la va a contestar. Si no responde, no se sabe (se asume que corre)."""
    try:
        return not get_status().get("running")
    except SodimacRpaError:
        return False


def _manual_note(row):
    if row.label_request_status != Request.MANUAL:
        return ""
    return f"El RPA no pudo generar la guía: {row.label_request_note or 'revisión manual'}"


def _location_mismatch(row, order, location_ids):
    if row.fulfillment_status not in ASSIGNED_STATUSES or not row.fulfillment_location_id:
        return f"Sin bodega en la plataforma: {row.fulfillment_note or 'sin asignar'}."
    if _invalid_location(row.fulfillment_location_name):
        return f"La bodega {row.fulfillment_location_name} no es válida para generar guía."
    if order is None:
        return "El pedido no está en la copia local de Shopify."
    if order.deleted_at or order.cancelled_at:
        return "El pedido está cancelado o borrado en Shopify."
    if not location_ids:
        return "El pedido no tiene bodega abierta en Shopify."
    if len(location_ids) > 1:
        return SPLIT_IN_SHOPIFY
    if location_ids[0] != row.fulfillment_location_id:
        return "La bodega de Shopify es distinta a la de la plataforma."
    return ""


def request_bulk_labels(purchase_orders=None, requested_by=None):
    """Confirmación de "Generar guías disponibles": vuelve a calcular la
    vista previa (pudo cambiar desde que se mostró) y manda al RPA, en un
    solo lote, las `ready`; con `purchase_orders` (las que la persona dejó
    marcadas), solo esas de entre las `ready`. Devuelve la vista previa con
    `sent` (las OC enviadas). `LabelRequestError` si no queda ninguna;
    `SodimacRpaBusy` / `SodimacRpaError` se propagan."""
    preview = bulk_label_preview()
    chosen = None if purchase_orders is None else {str(oc) for oc in purchase_orders}
    purchase_orders = [
        item["purchase_order"] for item in preview["ready"] if chosen is None or item["purchase_order"] in chosen
    ]
    if not purchase_orders:
        raise LabelRequestError("No hay OC disponibles para generar guía.")
    sent = request_sodimac_labels(purchase_orders, source=SodimacRpaRun.Source.BULK, requested_by=requested_by)
    return {**preview, "sent": sent}


def rpa_run_history(limit=20):
    """Historial del RPA: las últimas `limit` corridas, la más reciente
    primero, con sus OC."""
    items = SodimacRpaRunItem.objects.select_related("order")
    return list(
        SodimacRpaRun.objects.select_related("requested_by").prefetch_related(Prefetch("items", queryset=items))[:limit]
    )


def rpa_panel_status():
    """Recuadro "RPA de guías" del panel: si el RPA está configurado y
    responde, en qué va, y el enlace del visor para verlo en vivo. Nunca
    falla: un RPA caído se informa en `error`."""
    if not SODIMAC_RPA_URL:
        return {"configured": False, "reachable": False, "viewer_url": "", "error": "SODIMAC_RPA_URL no está configurada."}
    status = {"configured": True, "reachable": True, "viewer_url": viewer_url(SODIMAC_RPA_URL), "error": ""}
    try:
        data = get_status()
    except SodimacRpaError as error:
        return {**status, "reachable": False, "error": str(error)}
    pending = data.get("pending_intervention") or None
    return {
        **status,
        "running": bool(data.get("running")),
        "state": data.get("state") or "SIN_INICIAR",
        "message": data.get("message") or "",
        "updated_at": data.get("updated_at") or None,
        "waiting_human": bool(pending),
        "waiting_reason": (pending or {}).get("reason") or "",
    }


class RpaControlError(Exception):
    """El RPA no aceptó la orden (no hay corrida, no hay pausa); el texto es
    para el equipo."""


def rpa_continue():
    """Botón "Continuar": después de resolver el CAPTCHA en el visor."""
    try:
        continue_run()
    except SodimacRpaBusy as error:
        raise RpaControlError("El RPA no está esperando ninguna validación.") from error


def rpa_kill():
    """Botón "Detener": corta la corrida del RPA. Las OC que no alcanzó a
    procesar pasan a `error` cuando el RPA avisa el fin del lote, y se piden
    otra vez en el siguiente sync."""
    try:
        kill_run()
    except SodimacRpaBusy as error:
        raise RpaControlError("El RPA no tiene ninguna corrida en curso.") from error


def stock_dispatch(purchase_order):
    """Bodega desde la que sale la OC, para que el RPA elija la dirección de
    despacho en el portal. Es la que eligió la importación por stock
    (`MarketplaceOrder.fulfillment_location_name`, el nombre de la ubicación
    de Shopify: el RPA conoce "Bodega Envia", "Baru" y "Taumm").

    Devuelve (respuesta, código HTTP). Misma forma que la API de pamo_web
    que consumía el RPA: {"success", "oc", "bodega_despacho", "productos"}.
    """
    order = MarketplaceOrder.objects.filter(marketplace=SODIMAC, marketplace_order_id=str(purchase_order)).first()
    if order is None:
        return {"success": False, "message": f"No existe la OC {purchase_order}."}, 404
    if order.fulfillment_status not in ASSIGNED_STATUSES or not order.fulfillment_location_name:
        return {
            "success": False,
            "message": f"La OC {purchase_order} no tiene bodega: {order.fulfillment_note or 'sin asignar'}.",
        }, 409
    return {
        "success": True,
        "oc": order.marketplace_order_id,
        "bodega_despacho": order.fulfillment_location_name,
        "productos": [{"sku": item.marketplace_sku, "cantidad": item.quantity} for item in order.items.all()],
    }, 200


def process_rpa_event(data):
    """Webhook del RPA (`evento`):

    - `orden_procesada`: resultado de UNA OC, con rótulo y relación en
      base64 (la guía Home Delivery trae solo el rótulo) y el código
      `resultado` de lo que pasó (`MarketplaceOrder.LabelRequestResult`).
      Se guarda la guía (`MarketplaceLabel`) con el número leído del
      rótulo; el despacho la toma solo y avisa a la bodega.
    - `validacion_humana`: el RPA espera que una persona resuelva un CAPTCHA;
      se avisa por correo con el enlace del visor.
    - `proceso_finalizado`: fin del lote. Las OC que siguen `solicitada` sin
      respuesta pasan a `error` para pedirlas otra vez.

    Devuelve un texto corto con lo que se hizo.
    """
    if not isinstance(data, dict):
        raise RpaEventError("El cuerpo debe ser un objeto JSON.")
    event = data.get("evento")
    if event == "orden_procesada":
        return _order_processed(data)
    if event == "validacion_humana":
        return _human_validation(data)
    if event == "proceso_finalizado":
        return _batch_finished(data)
    raise RpaEventError(f"Evento desconocido: {event!r}.")


def _order_processed(data):
    purchase_order = str(data.get("numero_orden") or "").strip()
    order = MarketplaceOrder.objects.filter(marketplace=SODIMAC, marketplace_order_id=purchase_order).first()
    if order is None:
        raise RpaEventError(f"No existe la OC {purchase_order!r}.")
    message = str(data.get("mensaje") or "").strip()
    result = _result(data.get("resultado"), purchase_order)

    if not data.get("guia_generada"):
        return _mark_run(order, Request.ERROR, f"El RPA no generó la guía: {message or 'sin detalle'}.", result)
    label_pdf = _pdf(data.get("rotulo_pdf_base64"))
    relation_pdf = _pdf(data.get("relacion_pdf_base64"))
    if label_pdf is None:
        # Pasa cuando la guía ya estaba generada en el portal (el RPA no la
        # vuelve a descargar): hay que bajarla a mano.
        return _mark_run(
            order, Request.MANUAL,
            f"El RPA no devolvió el rótulo ({message or 'sin detalle'}). Descargarlo del portal y cargarlo a mano.",
            result,
        )
    try:
        tracking = store_label(order, label_pdf, relation_pdf or b"", tracking=data.get("numero_guia"), result=result)
    except LabelStoreError as error:
        return _mark_run(order, Request.MANUAL, str(error), result)
    _log_item(order, Request.RECEIVED, "", result, tracking)
    return f"{order.marketplace_order_id}: {Request.RECEIVED}"


def _result(value, purchase_order):
    """Código `resultado` del RPA, o "" si no viene o no se conoce (RPA
    anterior al campo, o un código nuevo que el backend aún no tiene)."""
    value = str(value or "").strip()
    if value in Result.values:
        return value
    if value:
        logger.warning("RPA de Sodimac: resultado desconocido %r para la OC %s", value, purchase_order)
    return ""


class LabelStoreError(Exception):
    """La guía no se puede guardar; el texto es para el equipo."""


def store_label(order, label_pdf, relation_pdf, *, tracking="", result=""):
    """Guarda la guía (rótulo + relación) de la OC `order`. La usan el
    webhook del RPA y la carga manual del panel ("Cargar guía"), así que las
    dos vías validan igual:

    - Los PDF son PDF y pesan hasta 5 MB.
    - El número es `tracking` o, si viene vacío, el que se lee del rótulo, y
      debe coincidir con el de la relación cuando esta lo trae.
    - Sin relación (`relation_pdf` vacío) solo se acepta la guía Home
      Delivery, cuyo número es el de la OC.
    - No se reemplaza una guía con la que la bodega ya fue avisada.

    Reemplaza la guía anterior si la hay. Deja la solicitud en `recibida`
    con el `result` del RPA ("" en la carga manual).
    """
    documents = [(label_pdf, "rótulo")] + ([(relation_pdf, "relación")] if relation_pdf else [])
    for content, name in documents:
        if not content or len(content) > MAX_PDF_BYTES or not bytes(content[:5]) == b"%PDF-":
            raise LabelStoreError(f"El archivo del {name} no es un PDF válido (o pesa más de 5 MB).")
    if Dispatch.objects.filter(shopify_order__shopify_id=order.shopify_order_id, status=Dispatch.Status.NOTIFIED).exists():
        raise LabelStoreError("La bodega ya fue avisada con la guía anterior; no se reemplaza.")
    tracking = str(tracking or "").strip() or read_label_tracking(label_pdf)
    if not tracking:
        raise LabelStoreError("No se encontró el número de guía (REMESA) en el rótulo: escribirlo a mano.")
    if len(tracking) > 64:
        raise LabelStoreError("El número de guía tiene más de 64 caracteres.")
    if relation_pdf:
        relation_tracking = read_label_tracking(relation_pdf)
        if relation_tracking and relation_tracking != tracking:
            raise LabelStoreError(f"El rótulo ({tracking}) y la relación ({relation_tracking}) traen números distintos.")
    elif tracking != order.marketplace_order_id:
        raise LabelStoreError(
            f"Falta la relación: solo se acepta sin relación la guía Home Delivery, cuyo número es el de la OC "
            f"(el rótulo trae {tracking})."
        )
    MarketplaceLabel.objects.update_or_create(
        order=order,
        defaults={"tracking_number": tracking, "label_pdf": label_pdf, "relation_pdf": bytes(relation_pdf or b"")},
    )
    _mark(order, Request.RECEIVED, "", result)
    return tracking


def upload_label_for_order(order, label_pdf, relation_pdf, *, tracking=""):
    """Botón "Cargar guía" del panel: una persona sube rótulo y relación de
    la OC de Sodimac del pedido de Shopify `order` (p. ej. la guía ya estaba
    generada en el portal o el RPA está caído). `LabelStoreError` si no
    aplica."""
    row = (
        MarketplaceOrder.objects.filter(marketplace=SODIMAC, shopify_order_id=order.shopify_id)
        .order_by("id")
        .first()
    )
    if row is None:
        raise LabelStoreError("El pedido no es una OC de Sodimac creada por este sistema.")
    return store_label(row, label_pdf, relation_pdf, tracking=tracking)


def _human_validation(data):
    viewer = data.get("viewer_url") or "(sin enlace del visor)"
    logger.warning("RPA de Sodimac espera validación humana: %s", viewer)
    if not SODIMAC_RPA_ALERT_EMAILS or not settings.DEFAULT_FROM_EMAIL:
        return "validación humana: sin destinatarios (SODIMAC_RPA_ALERT_EMAILS)"
    EmailMessage(
        subject="RPA Sodimac: resolver la validación para continuar",
        body=(
            "El RPA de guías de Sodimac está detenido esperando que una persona resuelva una validación "
            f"(CAPTCHA) en el portal de Homecenter.\n\nVisor: {viewer}\nMotivo: {data.get('reason') or '-'}\n\n"
            "Al resolverla, el RPA continúa solo con las órdenes pendientes."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(SODIMAC_RPA_ALERT_EMAILS),
    ).send(fail_silently=False)
    return "validación humana: aviso enviado"


def _batch_finished(data):
    processed = [str(oc) for oc in data.get("ordenes_procesadas") or []]
    if data.get("proceso_exitoso"):
        note, result = "El RPA terminó sin responder por esta OC.", Result.NO_RESPONSE
    else:
        note, result = "El RPA no pudo iniciar sesión en el portal.", Result.LOGIN_FAILED
    lost = MarketplaceOrder.objects.filter(marketplace=SODIMAC, label_request_status=Request.REQUESTED).exclude(
        marketplace_order_id__in=processed
    )
    count = lost.update(
        label_request_status=Request.ERROR, label_request_note=note, label_request_result=result,
        updated_at=timezone.now(),
    )
    run = SodimacRpaRun.objects.filter(status=Run.RUNNING).order_by("-id").first()
    if run is not None:
        run.items.filter(status=Request.REQUESTED).exclude(purchase_order__in=processed).update(
            status=Request.ERROR, note=note, result=result, updated_at=timezone.now()
        )
        run.status = Run.FINISHED if data.get("proceso_exitoso") else Run.LOGIN_FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
    return f"lote terminado: {count} OC sin respuesta pasan a error"


def _mark_run(order, status, note, result):
    """`_mark` de una respuesta del RPA: también queda en el historial."""
    _log_item(order, status, note, result)
    return _mark(order, status, note, result)


def _mark(order, status, note, result=None):
    fields = {"label_request_status": status, "label_request_note": note, "updated_at": timezone.now()}
    if result is not None:
        fields["label_request_result"] = result
    MarketplaceOrder.objects.filter(pk=order.pk).update(**fields)
    return f"{order.marketplace_order_id}: {status}"


def _pdf(value):
    if not value:
        return None
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        return None
    return content
