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
        return _notify_envia(dispatch, details, label)
    return notify_list(channel, dispatch.location, [(dispatch, details, label)])


def notify_list(channel, location, entries):
    """Un solo correo o WhatsApp a `location` con varios pedidos (aviso
    masivo por bodega): `entries` es [(dispatch, details, label)], todos de
    esa bodega. Envía no va aquí: es una orden por pedido (`notify`).
    Misma respuesta y errores que `notify`."""
    if channel == Channel.WHATSAPP:
        return _notify_whatsapp(location, entries)
    return _notify_email(location, entries)


def email_configured():
    """Hay remitente y un transporte: la API de Gmail (GMAIL_REFRESH_TOKEN)
    o SMTP (EMAIL_HOST). Ver EMAIL_BACKEND en config/constants.py."""
    return bool(settings.DEFAULT_FROM_EMAIL and (settings.GMAIL_REFRESH_TOKEN or settings.EMAIL_HOST))


def _notify_email(location, entries):
    """Correo con la misma tabla del listado en HTML (destinatario por
    pedido, imágenes enlazadas al CDN de Shopify, link firmado a la guía) y
    el texto plano de respaldo. La
    guía y la relación siguen adjuntas: por eso el link aquí es opcional
    (sin `BACKEND_PUBLIC_URL` va solo el número)."""
    if not email_configured():
        raise DispatchNotificationError(
            "Correo no configurado (DEFAULT_FROM_EMAIL y GMAIL_REFRESH_TOKEN o EMAIL_HOST)."
        )
    images = product_image_urls([item["sku"] for _, details, _ in entries for item in details["items"]])
    rows = []
    for dispatch, details, label in entries:
        try:
            guide_url = label_link(dispatch) if label else ""
        except LabelLinkError:
            guide_url = ""
        rows.append(_list_entry(details, label, guide_url, images))
    html = render_to_string("orders/email/dispatch_list.html", {
        "location_name": location.name,
        "orders": rows,
        "links_expire_on": links_expire_on() if any(row["label_url"] for row in rows) else "",
    })
    first = entries[0][1]["order_name"]
    message = EmailMultiAlternatives(
        subject=(
            f"Despacho pedido {first} - {location.name}"
            if len(entries) == 1
            else f"Despacho de {len(entries)} pedidos - {location.name}"
        ),
        body="\n\n----------\n\n".join(
            dispatch_text(details, location, label["tracking_number"] if label else "") for _, details, label in entries
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(location.emails),
    )
    message.attach_alternative(html, "text/html")
    for _, details, label in entries:
        if label:
            message.attach(f"guia-{details['order_name']}.pdf", label["pdf"], "application/pdf")
            if label.get("relation_pdf"):
                message.attach(f"relacion-{details['order_name']}.pdf", label["relation_pdf"], "application/pdf")
    message.send(fail_silently=False)
    return {"recipient": ", ".join(location.emails), "external_id": ""}


def _notify_whatsapp(location, entries):
    """Plantilla `despacho_bodega` con el listado en PDF (uno o varios pedidos) en el
    encabezado: la guía no va adjunta sino como link firmado en la tabla.
    El PDF se sube una vez a Zernio y lo usan todos los números."""
    # Un mensaje que inicia la empresa exige plantilla aprobada en Meta.
    template = WHATSAPP_TEMPLATES["despacho_bodega"]
    if not template["name"]:
        raise DispatchNotificationError(
            "Plantilla de WhatsApp sin nombre ('despacho_bodega' en orders/whatsapp_templates.py)."
        )
    guide_urls = []
    for dispatch, _, label in entries:
        try:
            guide_urls.append(label_link(dispatch) if label else "")
        except LabelLinkError as error:
            raise DispatchNotificationError(str(error)) from error
    images = product_images([item["sku"] for _, details, _ in entries for item in details["items"]])
    pdf = dispatch_list_pdf(
        location.name,
        [_list_entry(details, label, url, images) for (_, details, label), url in zip(entries, guide_urls)],
    )
    first = str(entries[0][1]["order_name"]).lstrip("#")
    filename = f"despacho-{first}.pdf" if len(entries) == 1 else f"despacho-{first}-y-{len(entries) - 1}-mas.pdf"
    document_url = upload_media(pdf, filename)
    body = [location.name, str(len(entries))]
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


def _notify_envia(dispatch, details, label):
    """Orden en Envía Fulfillment para la bodega del despacho.

    1. Si Envía ya la tiene (con nuestro identificador o con el número de
       Shopify, que es como la importa la tienda de Shopify conectada), se
       vincula esa orden y, si no tiene guía y aquí sí, se le carga
       (`add_order_tracking`). Nunca se crea una segunda.
    2. Si no, se crea (`create_order`) con el `variantId` de cada SKU, la
       dirección de entrega y la guía.

    Despacho de un pedido repartido (`split`): la orden que importa la
    tienda conectada trae el pedido **completo**, y Envía no la despacha si
    le falta algo (los productos de la otra bodega). Entonces solo se busca
    nuestro identificador con sufijo (`<número>-<despacho>`) y, si no está,
    se crea una orden solo con los productos de esta bodega. La importada
    queda sin despachar; operaciones decide si la cancela (decisión del
    usuario, 2026-10-21).
    """
    location, order = dispatch.location, dispatch.shopify_order
    identifier = dispatch_envia_identifier(dispatch, details)
    since = order.shopify_created_at or order.synced_at
    candidates = [identifier] if dispatch.split else [identifier, str(order.name).lstrip("#")]
    for candidate in dict.fromkeys(candidates):
        existing = find_order(candidate, since=since)
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
        identifier=identifier,
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


def dispatch_envia_identifier(dispatch, details):
    """Identificador del despacho en Envía: `_envia_identifier` y, en un
    pedido repartido, con el id del despacho (`20577-105`), para que cada
    bodega tenga su orden."""
    identifier = _envia_identifier(details)
    return f"{identifier}-{dispatch.pk}" if dispatch.split else identifier


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
