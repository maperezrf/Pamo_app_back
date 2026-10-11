import base64
import binascii
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.utils import timezone

from config.constants import SODIMAC_RPA_ALERT_EMAILS, SODIMAC_RPA_URL
from integrations.sodimac_rpa.client import SodimacRpaBusy
from integrations.sodimac_rpa.functions.trigger_labels import trigger_labels

from ..models import Dispatch, MarketplaceLabel, MarketplaceOrder
from .assign_dispatch_location import ASSIGNED_STATUSES
from .read_label_tracking import read_label_tracking

logger = logging.getLogger(__name__)

SODIMAC = MarketplaceOrder.Marketplace.SODIMAC
FINAL_STATUS = "4-ESTADO FINAL"
Request = MarketplaceOrder.LabelRequestStatus
# Una solicitud sin respuesta después de esto se da por perdida (el RPA se
# reinició, falló el webhook...) y se vuelve a pedir.
STALE_REQUEST = timedelta(hours=6)
MAX_PDF_BYTES = 5 * 1024 * 1024


class RpaEventError(Exception):
    """El evento del RPA no se puede procesar (payload inválido)."""


def request_sodimac_labels(purchase_orders=None):
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
        stale = timezone.now() - STALE_REQUEST
        candidates = candidates.exclude(marketplace_status=FINAL_STATUS).filter(
            Q(label_request_status__in=["", Request.ERROR])
            | Q(label_request_status=Request.REQUESTED, label_requested_at__lt=stale)
        )
    pending = list(candidates.values_list("marketplace_order_id", flat=True))
    if not pending:
        return []
    try:
        trigger_labels(pending)
    except SodimacRpaBusy:
        if purchase_orders is not None:
            raise
        logger.info("RPA de Sodimac ocupado; %s OC quedan para la próxima corrida", len(pending))
        return []
    MarketplaceOrder.objects.filter(marketplace=SODIMAC, marketplace_order_id__in=pending).update(
        label_request_status=Request.REQUESTED,
        label_request_note="",
        label_requested_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return pending


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
      base64. Se guarda la guía (`MarketplaceLabel`) con el número leído del
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

    if not data.get("guia_generada"):
        return _mark(order, Request.ERROR, f"El RPA no generó la guía: {message or 'sin detalle'}.")
    label_pdf = _pdf(data.get("rotulo_pdf_base64"))
    relation_pdf = _pdf(data.get("relacion_pdf_base64"))
    if label_pdf is None or relation_pdf is None:
        # Pasa cuando la guía ya estaba generada en el portal (el RPA no la
        # vuelve a descargar): hay que bajarla a mano.
        return _mark(
            order, Request.MANUAL,
            f"El RPA no devolvió los dos PDF ({message or 'sin detalle'}). Descargarlos del portal y cargarlos a mano.",
        )
    if Dispatch.objects.filter(shopify_order__shopify_id=order.shopify_order_id, status=Dispatch.Status.NOTIFIED).exists():
        return _mark(order, Request.MANUAL, "Llegó otra guía, pero la bodega ya había sido avisada con la anterior.")

    tracking = str(data.get("numero_guia") or "").strip() or read_label_tracking(label_pdf)
    relation_tracking = read_label_tracking(relation_pdf)
    if not tracking:
        return _mark(order, Request.MANUAL, "No se encontró el número de guía (REMESA) en el rótulo.")
    if relation_tracking and relation_tracking != tracking:
        return _mark(
            order, Request.MANUAL,
            f"El rótulo ({tracking}) y la relación ({relation_tracking}) traen números distintos.",
        )
    MarketplaceLabel.objects.update_or_create(
        order=order, defaults={"tracking_number": tracking, "label_pdf": label_pdf, "relation_pdf": relation_pdf}
    )
    return _mark(order, Request.RECEIVED, "")


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
    note = (
        "El RPA terminó sin responder por esta OC." if data.get("proceso_exitoso")
        else "El RPA no pudo iniciar sesión en el portal."
    )
    lost = MarketplaceOrder.objects.filter(marketplace=SODIMAC, label_request_status=Request.REQUESTED).exclude(
        marketplace_order_id__in=processed
    )
    count = lost.update(label_request_status=Request.ERROR, label_request_note=note, updated_at=timezone.now())
    return f"lote terminado: {count} OC sin respuesta pasan a error"


def _mark(order, status, note):
    MarketplaceOrder.objects.filter(pk=order.pk).update(
        label_request_status=status, label_request_note=note, updated_at=timezone.now()
    )
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
