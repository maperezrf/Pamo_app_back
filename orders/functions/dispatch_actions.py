import hashlib

from django.utils import timezone

from integrations.envia.functions.create_label import LabelUnknownResult, create_label
from integrations.envia.functions.download_label import download_label
from integrations.envia.functions.quote import quote
from integrations.envia.functions.resolve_colombia_city import resolve_colombia_city
from integrations.shopify.functions.get_order_shipping_address import get_order_shipping_address

from ..models import Dispatch
from .dispatch_orders import ensure_dispatch, process_order_dispatch
from .fetch_dispatch_label import CHANNEL_LABELS, fetch_dispatch_label

# Paquete por defecto mientras Shopify no tenga peso y medidas reales: el de
# referencia de pamo-one-engineering (1 kg, 10×10×10 cm).
DEFAULT_PACKAGE = {"weight": 1, "dimensions": {"length": 10, "width": 10, "height": 10}}
DISPATCH_EMAIL = "despachos@pamo.co"


class DispatchActionError(Exception):
    """La acción no se puede hacer para este pedido; el texto es para el
    equipo (se muestra en el panel)."""


def notify_dispatch_now(order):
    """Botón "Notificar a proveedor": avanza el despacho en modo manual
    (trae la guía y avisa por los canales de la bodega). Ver
    `process_order_dispatch(manual=True)`."""
    ensure_dispatch(order)
    process_order_dispatch(order, manual=True)
    return Dispatch.objects.select_related("location").get(shopify_order=order)


def fetch_channel_label_now(order):
    """Botón "Traer guía" (Mercado Libre, Falabella): la pide al canal y la
    deja registrada en el despacho. Ojo: en Mercado Libre una guía sin
    imprimir queda marcada como impresa."""
    dispatch = _dispatch_with_location(order)
    if order.marketplace not in CHANNEL_LABELS:
        raise DispatchActionError("Este canal no trae guía propia: hay que generarla en Envía.")
    label, reason = fetch_dispatch_label(order, force=True)
    if label is None:
        raise DispatchActionError(reason)
    dispatch.tracking_number = label["tracking_number"]
    dispatch.label_source = Dispatch.LabelSource.CHANNEL
    dispatch.label_sha256 = hashlib.sha256(label["pdf"]).hexdigest()
    dispatch.label_fetched_at = timezone.now()
    dispatch.save()
    return dispatch


def quote_dispatch_label(order):
    """Botón "Generar guía", paso 1 (no cobra): opciones de Envía Shipping
    desde la bodega del despacho hasta la dirección de envío del pedido."""
    dispatch = _dispatch_for_generation(order)
    return quote(_shipping_payload(order, dispatch.location))


def generate_dispatch_label(order, *, carrier, service):
    """Botón "Generar guía", paso 2 (**cobra**): genera la guía de la opción
    elegida, la baja y la deja en el despacho. Nunca genera una segunda.
    Si el resultado es incierto, lo anota en el despacho y no reintenta."""
    dispatch = _dispatch_for_generation(order)
    option = {"providerPayload": {"carrier": carrier, "service": service, "type": 1}}
    reference = f"pamo-{str(order.name).lstrip('#')}"
    try:
        result = create_label(_shipping_payload(order, dispatch.location), option, order_reference=reference)
    except LabelUnknownResult as error:
        dispatch.note = (
            f"Resultado incierto al generar la guía ({error}). Revisar en Envía si existe una con la referencia "
            f"{reference} antes de reintentar."
        )
        dispatch.save()
        raise
    pdf = download_label(result["label_url"])
    dispatch.tracking_number = result["tracking_number"]
    dispatch.label_source = Dispatch.LabelSource.ENVIA
    dispatch.label_url = result["label_url"]
    dispatch.label_carrier = result["carrier"]
    dispatch.label_service = result["service"]
    dispatch.label_sha256 = hashlib.sha256(pdf).hexdigest()
    dispatch.label_fetched_at = timezone.now()
    dispatch.note = ""
    dispatch.save()
    return dispatch


def _dispatch_with_location(order):
    dispatch = ensure_dispatch(order)
    if dispatch.location is None:
        raise DispatchActionError(dispatch.note or "El pedido no tiene bodega asignada.")
    return dispatch


def _dispatch_for_generation(order):
    dispatch = _dispatch_with_location(order)
    if order.marketplace in CHANNEL_LABELS:
        raise DispatchActionError("Este canal trae su propia guía: usar \"Traer guía\".")
    if dispatch.location.creates_own_label:
        raise DispatchActionError(f"{dispatch.location.name} crea su propia guía: no se genera en Envía.")
    if dispatch.label_source == Dispatch.LabelSource.ENVIA:
        raise DispatchActionError(f"Ya tiene una guía generada ({dispatch.tracking_number}); no se genera otra.")
    return dispatch


def _shipping_payload(order, location):
    if not location.origin_address or not location.origin_phone:
        raise DispatchActionError(f"A {location.name} le falta dirección o teléfono en Shopify (sincronizar bodegas).")
    shipping = get_order_shipping_address(order.shopify_id)
    if not shipping or not shipping["address1"] or not shipping["city"]:
        raise DispatchActionError("El pedido no tiene dirección de envío en Shopify.")
    origin_city = resolve_colombia_city(location.city, location.origin_province_code)
    destination_city = resolve_colombia_city(shipping["city"], shipping["province_code"])
    names = ", ".join(line.name for line in order.lines.all() if line.name)
    return {
        "origin": {
            "name": location.name, "company": "Pamo", "phone": location.origin_phone, "email": DISPATCH_EMAIL,
            "street": location.origin_address, "city": origin_city["city"], "state": origin_city["state"],
            "country": "CO", "postalCode": location.origin_zip,
        },
        "destination": {
            "name": " ".join(part for part in (shipping["first_name"], shipping["last_name"]) if part) or "-",
            "company": shipping["company"], "phone": shipping["phone"] or order.phone, "email": order.email,
            "street": " ".join(part for part in (shipping["address1"], shipping["address2"]) if part),
            "city": destination_city["city"], "state": destination_city["state"], "country": "CO",
            "postalCode": shipping["zip"],
        },
        "packages": [
            {
                "type": "box",
                "content": (names or f"Pedido {order.name}")[:180],
                "amount": 1,
                "declaredValue": float(order.total or 0),
                **DEFAULT_PACKAGE,
            }
        ],
    }
