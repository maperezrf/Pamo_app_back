from django.conf import settings
from django.core.mail import EmailMessage

from config.constants import (
    DISPATCH_ENVIA_FALLBACK_EMAIL,
    DISPATCH_WHATSAPP_TEMPLATE,
    DISPATCH_WHATSAPP_TEMPLATE_LANGUAGE,
)
from integrations.envia.functions.resolve_colombia_state import resolve_colombia_state
from integrations.envia_fulfillment.functions.add_order_tracking import add_order_tracking
from integrations.envia_fulfillment.functions.create_order import create_order
from integrations.envia_fulfillment.functions.find_order import find_order
from integrations.envia_fulfillment.functions.find_variant_ids import find_variant_ids
from integrations.shopify.functions.get_order_shipping_address import get_order_shipping_address
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
    if channel == Channel.API:
        return _notify_envia(dispatch.location, details, label, dispatch.shopify_order)
    senders = {Channel.EMAIL: _notify_email, Channel.WHATSAPP: _notify_whatsapp}
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


def _notify_envia(location, details, label, order):
    """Orden en Envía Fulfillment para la bodega `location`.

    1. Si Envía ya la tiene (con nuestro identificador o con el número de
       Shopify, que es como la importa la tienda de Shopify conectada), se
       vincula esa orden y, si no tiene guía y aquí sí, se le carga
       (`add_order_tracking`). Nunca se crea una segunda.
    2. Si no, se crea (`create_order`) con el `variantId` de cada SKU, la
       dirección de entrega y la guía.
    """
    since = order.shopify_created_at or order.synced_at
    for identifier in dict.fromkeys([_envia_identifier(details), str(order.name).lstrip("#")]):
        existing = find_order(identifier, since=since)
        if existing:
            if label and not existing["pretrackings"]:
                add_order_tracking(existing["order_id"], tracking_number=label["tracking_number"], label_pdf=label["pdf"])
            return {
                "recipient": f"Envía bodega {location.envia_warehouse_id} (orden existente de {existing['shop_name'] or 'Envía'})",
                "external_id": str(existing["order_id"]),
            }

    products = _envia_products(details)  # primero: sin productos en Envía no hay orden
    address = _envia_address(details, order)
    email = details["buyer"]["email"] or DISPATCH_ENVIA_FALLBACK_EMAIL
    if not email:
        raise DispatchNotificationError("Envía exige correo y el pedido no lo trae (DISPATCH_ENVIA_FALLBACK_EMAIL).")
    created = create_order(
        identifier=_envia_identifier(details),
        warehouse_id=location.envia_warehouse_id,
        products=products,
        shipping_address=address,
        email=email,
        total=details["total"] or 0,
        tracking_number=label["tracking_number"] if label else "",
        label_pdf=label["pdf"] if label else None,
    )
    return {"recipient": f"Envía bodega {location.envia_warehouse_id}", "external_id": str(created["order_id"])}


def _envia_address(details, order):
    """Dirección de entrega para Envía. Tienda web: la de envío del pedido en
    Shopify (la copia local solo tiene la del cliente). Marketplace: la del
    comprador guardada en `MarketplaceOrder`."""
    buyer = details["buyer"]
    if details["channel"] == "shopify":
        shipping = get_order_shipping_address(order.shopify_id) or {}
        first, last = shipping.get("first_name", ""), shipping.get("last_name", "")
        street = " ".join(part for part in (shipping.get("address1"), shipping.get("address2")) if part)
        city, region, phone = shipping.get("city", ""), shipping.get("province_code") or shipping.get("province", ""), shipping.get("phone", "")
    else:
        first, last = buyer["first_name"], buyer["last_name"]
        street, city, region, phone = buyer["address"], buyer["city"], buyer["region"], buyer["phone"]
    if not last and " " in first.strip():
        first, _, last = first.strip().partition(" ")
    missing = [label for label, value in (("nombre", first), ("dirección", street), ("ciudad", city), ("departamento", region)) if not value]
    if missing:
        raise DispatchNotificationError(f"Faltan datos de entrega para Envía: {', '.join(missing)}.")
    return {
        "first_name": first.strip(),
        "last_name": (last or first).strip(),
        "address1": street.strip(),
        "identification": buyer["identification"],
        "state_code": resolve_colombia_state(region),
        "state_name": region,
        "city": city.strip(),
        "phone": phone or buyer["phone"],
        "references": f"Pedido {details['order_name']}",
    }


def _envia_identifier(details):
    # Solo el número que operaciones reconoce: el del pedido en el canal
    # (OC de Sodimac, número de Mercado Libre...) o, en la tienda web, el de
    # Shopify. Sin prefijo de canal: operaciones lo pidió así (2026-10-08)
    # tras ver "sodimac-16200704" en el panel. Envía no deja cambiarlo
    # después (409 una vez la orden está en bodega). La unicidad la da el
    # `DispatchNotification` de este backend, no el identificador.
    numbers = details["marketplace_numbers"]
    return str(numbers[0]) if numbers else str(details["order_name"]).lstrip("#")


def _envia_products(details):
    """Líneas para Envía con su `variantId` (el SKU es el mismo de Shopify;
    `find_variant_ids` lee el inventario de Envía)."""
    skus = [item["sku"] for item in details["items"]]
    variants = find_variant_ids(skus)
    missing = sorted({sku or "(sin SKU)" for sku in skus if sku not in variants})
    if missing:
        raise DispatchNotificationError(f"SKU sin producto en Envía: {', '.join(missing)}.")
    return [
        {"variant_id": variants[item["sku"]]["variant_id"], "quantity": item["quantity"], "price": item["unit_price"] or 0}
        for item in details["items"]
    ]
