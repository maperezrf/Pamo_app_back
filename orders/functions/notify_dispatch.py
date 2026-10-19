from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from config.constants import DISPATCH_ENVIA_FALLBACK_EMAIL
from integrations.envia.functions.resolve_colombia_state import resolve_colombia_state
from integrations.envia_fulfillment.functions.add_order_comment import add_order_comment
from integrations.envia_fulfillment.functions.add_order_tracking import add_order_tracking
from integrations.envia_fulfillment.functions.create_order import create_order
from integrations.envia_fulfillment.functions.find_order import find_order
from integrations.envia_fulfillment.functions.find_variant_ids import find_variant_ids
from integrations.shopify.functions.get_order_shipping_address import get_order_shipping_address
from integrations.zernio.functions.send_whatsapp_template import send_whatsapp_template
from integrations.zernio.functions.upload_media import upload_media

from ..models import DispatchLocation
from ..whatsapp_templates import WHATSAPP_TEMPLATES
from .dispatch_details import channel_text, dispatch_text, recipient_lines
from .dispatch_list_pdf import dispatch_list_pdf
from .label_links import LabelLinkError, label_link, links_expire_on
from .product_images import product_image_urls, product_images

Channel = DispatchLocation.Channel


class DispatchNotificationError(Exception):
    """El aviso no se puede enviar por falta de configuración o de datos
    (no por un fallo del proveedor). El texto es legible para el equipo."""


def notify(channel, *, dispatch, details, label):
    """Avisa a la bodega del despacho por `channel`. `label` es
    {"pdf", "tracking_number"} o None (bodega que no exige guía).

    Devuelve {"recipient": str, "external_id": str} y, si algo secundario
    falló sin impedir el aviso, "warning". Un error del proveedor o
    `DispatchNotificationError` se propaga; quien llama lo registra en el
    aviso.
    """ 
    if channel == Channel.API:
        return _notify_envia(dispatch.location, details, label, dispatch.shopify_order)
    if channel == Channel.WHATSAPP:
        return _notify_whatsapp(dispatch, details, label)
    return _notify_email(dispatch, details, label)


def email_configured():
    """Hay remitente y un transporte: la API de Gmail (GMAIL_REFRESH_TOKEN)
    o SMTP (EMAIL_HOST). Ver EMAIL_BACKEND en config/constants.py."""
    return bool(settings.DEFAULT_FROM_EMAIL and (settings.GMAIL_REFRESH_TOKEN or settings.EMAIL_HOST))


def _notify_email(dispatch, details, label):
    """Correo con la misma tabla del listado en HTML (destinatario por
    pedido, imágenes enlazadas al CDN de Shopify, link firmado a la guía) y
    el texto plano de respaldo. La
    guía y la relación siguen adjuntas: por eso el link aquí es opcional
    (sin `BACKEND_PUBLIC_URL` va solo el número)."""
    if not email_configured():
        raise DispatchNotificationError(
            "Correo no configurado (DEFAULT_FROM_EMAIL y GMAIL_REFRESH_TOKEN o EMAIL_HOST)."
        )
    location = dispatch.location
    tracking_number = label["tracking_number"] if label else ""
    try:
        guide_url = label_link(dispatch) if label else ""
    except LabelLinkError:
        guide_url = ""
    images = product_image_urls([item["sku"] for item in details["items"]])
    html = render_to_string("orders/email/dispatch_list.html", {
        "location_name": location.name,
        "orders": [_list_entry(details, label, guide_url, images)],
        "links_expire_on": links_expire_on() if guide_url else "",
    })
    message = EmailMultiAlternatives(
        subject=f"Despacho pedido {details['order_name']} - {location.name}",
        body=dispatch_text(details, location, tracking_number),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(location.emails),
    )
    message.attach_alternative(html, "text/html")
    if label:
        message.attach(f"guia-{details['order_name']}.pdf", label["pdf"], "application/pdf")
        if label.get("relation_pdf"):
            message.attach(f"relacion-{details['order_name']}.pdf", label["relation_pdf"], "application/pdf")
    message.send(fail_silently=False)
    return {"recipient": ", ".join(location.emails), "external_id": ""}


def _notify_whatsapp(dispatch, details, label):
    """Plantilla `despacho_bodega` con el listado en PDF (un pedido) en el
    encabezado: la guía no va adjunta sino como link firmado en la tabla.
    El PDF se sube una vez a Zernio y lo usan todos los números."""
    # Un mensaje que inicia la empresa exige plantilla aprobada en Meta.
    template = WHATSAPP_TEMPLATES["despacho_bodega"]
    if not template["name"]:
        raise DispatchNotificationError(
            "Plantilla de WhatsApp sin nombre ('despacho_bodega' en orders/whatsapp_templates.py)."
        )
    location = dispatch.location
    try:
        guide_url = label_link(dispatch) if label else ""
    except LabelLinkError as error:
        raise DispatchNotificationError(str(error)) from error
    images = product_images([item["sku"] for item in details["items"]])
    pdf = dispatch_list_pdf(location.name, [_list_entry(details, label, guide_url, images)])
    filename = f"despacho-{str(details['order_name']).lstrip('#')}.pdf"
    document_url = upload_media(pdf, filename)
    body = [location.name, "1"]
    # Se envía a todos antes de fallar: si uno falla, el error dice a
    # quiénes sí llegó (un reintento les volvería a escribir).
    sent, failed = [], []
    for number in location.whatsapp_numbers:
        try:
            result = send_whatsapp_template(
                number,
                template["name"],
                language=template["language"],
                body_params=body,
                document_url=document_url,
                document_filename=filename,
            )
            sent.append((number, result["message_id"]))
        except Exception as error:  # noqa: BLE001 -- se reporta por número
            failed.append(f"{number}: {type(error).__name__}: {error}")
    if failed:
        raise DispatchNotificationError(
            f"WhatsApp falló para {'; '.join(failed)}. Enviado a: {', '.join(n for n, _ in sent) or 'nadie'}."
        )
    return {"recipient": ", ".join(n for n, _ in sent), "external_id": ",".join(m for _, m in sent)}


def _list_entry(details, label, guide_url, images):
    """Un pedido del listado (PDF y correo): ver `dispatch_list_pdf`.
    `images` es `{sku: imagen}`: bytes para el PDF, URL para el correo."""
    return {
        "order_name": details["order_name"],
        "channel": channel_text(details),
        "recipient": recipient_lines(details),
        "items": [{**item, "image": images.get(item["sku"])} for item in details["items"]],
        "tracking_number": label["tracking_number"] if label else "",
        "label_url": guide_url,
    }


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
            result = {
                "recipient": f"Envía bodega {location.envia_warehouse_id} (orden existente de {existing['shop_name'] or 'Envía'})",
                "external_id": str(existing["order_id"]),
            }
            if label and not existing["pretrackings"]:
                add_order_tracking(existing["order_id"], tracking_number=label["tracking_number"], label_pdf=label["pdf"])
                if label.get("relation_pdf"):
                    result["warning"] = _add_relation(existing["order_id"], details, label)
            return result

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
    result = {"recipient": f"Envía bodega {location.envia_warehouse_id}", "external_id": str(created["order_id"])}
    if label and label.get("relation_pdf"):
        result["warning"] = _add_relation(created["order_id"], details, label)
    return result


def _add_relation(order_id, details, label):
    """Carga la relación (remesa), que va aparte del rótulo: la API solo la
    acepta como comentario con archivo. Devuelve "" o una advertencia. Un
    fallo no hace fallar el aviso: la orden ya quedó creada con su guía y
    un reintento la vincularía sin volver a cargar la relación. La
    advertencia queda en el aviso para cargarla a mano."""
    try:
        add_order_comment(
            order_id,
            comment=f"Relación de la guía {label['tracking_number']}",
            pdf=label["relation_pdf"],
            filename=f"relacion-{details['order_name']}.pdf",
        )
    except Exception as error:  # noqa: BLE001 -- queda como advertencia del aviso
        return (
            f"La relación no se cargó en Envía ({type(error).__name__}: {error}). "
            "Cargarla a mano en la orden como comentario."
        )
    return ""


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
