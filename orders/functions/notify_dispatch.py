from django.conf import settings
from django.core.mail import EmailMessage

from config.constants import (
    DISPATCH_ENVIA_FALLBACK_EMAIL,
    DISPATCH_WHATSAPP_TEMPLATE,
    DISPATCH_WHATSAPP_TEMPLATE_LANGUAGE,
)
from integrations.envia_fulfillment.functions.create_order import create_order
from integrations.whatsapp.functions.send_template_message import send_template_message
from integrations.whatsapp.functions.upload_document import upload_document

from ..models import DispatchLocation
from .dispatch_details import dispatch_text, items_summary

Channel = DispatchLocation.Channel


class DispatchNotificationError(Exception):
    """El aviso no se puede enviar por falta de configuración o de datos
    (no por un fallo del proveedor). El texto es legible para el equipo."""


def notify(channel, *, dispatch, details, label):
    """Avisa a la bodega del despacho por `channel`. `label` es
    {"pdf", "tracking_number"} o None (bodega que no exige guía).

    Devuelve {"recipient": str, "external_id": str}. Un error del
    proveedor o `DispatchNotificationError` se propaga; quien llama lo
    registra en el aviso.
    """
    senders = {Channel.API: _notify_envia, Channel.EMAIL: _notify_email, Channel.WHATSAPP: _notify_whatsapp}
    return senders[channel](dispatch.location, details, label)


def _notify_email(location, details, label):
    if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        raise DispatchNotificationError("Correo no configurado (EMAIL_HOST / DEFAULT_FROM_EMAIL).")
    tracking_number = label["tracking_number"] if label else ""
    message = EmailMessage(
        subject=f"Despacho pedido {details['order_name']} - {location.name}",
        body=dispatch_text(details, location, tracking_number),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(location.emails),
    )
    if label:
        message.attach(f"guia-{details['order_name']}.pdf", label["pdf"], "application/pdf")
    message.send(fail_silently=False)
    return {"recipient": ", ".join(location.emails), "external_id": ""}


def _notify_whatsapp(location, details, label):
    # Un mensaje que inicia la empresa exige plantilla aprobada en Meta.
    if not DISPATCH_WHATSAPP_TEMPLATE:
        raise DispatchNotificationError("Plantilla de WhatsApp no configurada (DISPATCH_WHATSAPP_TEMPLATE).")
    media_id = upload_document(label["pdf"], f"guia-{details['order_name']}.pdf") if label else ""
    body = [details["order_name"], items_summary(details), details["buyer"]["city"] or "-"]
    # Se envía a todos antes de fallar: si uno falla, el error dice a
    # quiénes sí llegó (un reintento les volvería a escribir).
    sent, failed = [], []
    for number in location.whatsapp_numbers:
        try:
            result = send_template_message(
                number,
                DISPATCH_WHATSAPP_TEMPLATE,
                language=DISPATCH_WHATSAPP_TEMPLATE_LANGUAGE,
                body_params=body,
                document_media_id=media_id,
            )
            sent.append((number, result["message_id"]))
        except Exception as error:  # noqa: BLE001 -- se reporta por número
            failed.append(f"{number}: {type(error).__name__}: {error}")
    if failed:
        raise DispatchNotificationError(
            f"WhatsApp falló para {'; '.join(failed)}. Enviado a: {', '.join(n for n, _ in sent) or 'nadie'}."
        )
    return {"recipient": ", ".join(n for n, _ in sent), "external_id": ",".join(m for _, m in sent)}


def _notify_envia(location, details, label):
    products = _envia_products(details)
    buyer = details["buyer"]
    email = buyer["email"] or DISPATCH_ENVIA_FALLBACK_EMAIL
    if not email:
        raise DispatchNotificationError("Envía exige correo y el pedido no lo trae (DISPATCH_ENVIA_FALLBACK_EMAIL).")
    created = create_order(
        identifier=_envia_identifier(details),
        warehouse_id=location.envia_warehouse_id,
        products=products,
        shipping_address={
            "first_name": buyer["first_name"] or "-",
            "last_name": buyer["last_name"],
            "address1": buyer["address"] or "-",
            "identification": buyer["identification"],
            "state_name": buyer["region"] or "-",
            "city": buyer["city"] or "-",
            "phone": buyer["phone"],
        },
        email=email,
        total=details["total"] or 0,
        tracking_number=label["tracking_number"] if label else "",
        label_pdf=label["pdf"] if label else None,
    )
    return {"recipient": f"Envía bodega {location.envia_warehouse_id}", "external_id": str(created["order_id"])}


def _envia_identifier(details):
    # `<canal>-<número>`, igual que la etiqueta de idempotencia de Shopify;
    # en la tienda web, el número del pedido de Shopify.
    numbers = details["marketplace_numbers"]
    return f"{details['channel']}-{numbers[0]}" if numbers else f"shopify-{details['order_name']}"


def _envia_products(details):
    # Envía pide su propio `variantId` (por tienda), no el SKU. El cruce
    # está pendiente de verificar con la tienda principal de Envía (ver
    # docs/implementations-plans/envia-fulfillment-dispatch.md).
    raise DispatchNotificationError(
        "Cruce SKU → variantId de Envía pendiente: el aviso por API de Envía aún no se puede enviar."
    )
