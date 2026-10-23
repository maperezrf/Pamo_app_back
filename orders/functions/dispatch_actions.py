import hashlib
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from integrations.envia.functions.create_label import LabelUnknownResult, create_label
from integrations.envia.functions.download_label import download_label
from integrations.envia.functions.quote import quote
from integrations.envia.functions.resolve_colombia_city import resolve_colombia_city
from integrations.shopify.functions.get_order_shipping_address import get_order_shipping_address

from ..models import STORED_LABEL_SOURCES, Dispatch, MarketplaceOrder
from .dispatch_details import dispatch_lines, lines_total
from .dispatch_orders import ensure_dispatches, process_dispatch, reopen_manual_dispatch, stored_envia_label
from .fetch_dispatch_label import CHANNEL_LABELS, fetch_dispatch_label
from .shopify_order_marketplace import SHOPIFY_CHANNEL
from .sodimac_rpa import MAX_PDF_BYTES

# Paquete de la guía (kg y cm) cuando el panel no manda peso y medidas: el de
# referencia de pamo-one-engineering (1 kg, 10×10×10 cm), una caja.
DEFAULT_PACKAGE = {"weight": 1, "length": 10, "width": 10, "height": 10, "quantity": 1}
PACKAGE_FIELDS = {"weight": "El peso (kg)", "length": "El largo (cm)", "width": "El ancho (cm)", "height": "El alto (cm)"}
# Filas de paquetes (la API de Envía acepta hasta 20) y cajas iguales por fila.
MAX_PACKAGE_ROWS = 20
MAX_PACKAGE_QUANTITY = 50
DISPATCH_EMAIL = "despachos@pamo.co"


class DispatchActionError(Exception):
    """La acción no se puede hacer para este pedido; el texto es para el
    equipo (se muestra en el panel)."""


class DispatchNotFound(Exception):
    """El despacho pedido no es de este pedido."""


def dispatch_for_action(order, dispatch_id=None, *, create=True):
    """El despacho sobre el que actúa un botón del panel, creando los del
    pedido si no existen (`ensure_dispatches`; con `create=False`, solo los
    que ya hay). Con un solo despacho, `dispatch_id` no hace falta; si el
    pedido sale de varias bodegas, sí."""
    if create:
        dispatches = ensure_dispatches(order)
    else:
        dispatches = list(Dispatch.objects.filter(shopify_order=order).select_related("location").order_by("id"))
        if not dispatches:
            raise DispatchNotFound(f"El pedido {order.name} no tiene despacho.")
    if dispatch_id not in (None, ""):
        for dispatch in dispatches:
            if str(dispatch.pk) == str(dispatch_id):
                return dispatch
        raise DispatchNotFound(f"El despacho {dispatch_id} no es del pedido {order.name}.")
    if len(dispatches) > 1:
        raise DispatchActionError(
            "El pedido sale de varias bodegas: ya tiene un despacho por bodega. Recarga y usa los botones de cada una."
        )
    return dispatches[0]


def notify_dispatch_now(dispatch):
    """Botón "Notificar a proveedor": avanza el despacho ya (trae la guía y
    avisa por los canales de la bodega), sin esperar al proceso. Un
    despacho en gestión manual se vuelve a evaluar. En un pedido repartido
    avisa solo a la bodega de este despacho, con sus productos."""
    reopen_manual_dispatch(dispatch)
    process_dispatch(dispatch)
    return dispatch


def fetch_channel_label_now(dispatch):
    """Botón "Traer guía" (Mercado Libre, Falabella): la pide al canal y la
    deja registrada en el despacho. Ojo: en Mercado Libre una guía sin
    imprimir queda marcada como impresa."""
    order = dispatch.shopify_order
    _require_location(dispatch)
    if order.marketplace not in CHANNEL_LABELS:
        raise DispatchActionError("Este canal no trae guía propia: hay que generarla en Envía.")
    label, reason = fetch_dispatch_label(order)
    if label is None:
        raise DispatchActionError(reason)
    dispatch.tracking_number = label["tracking_number"]
    dispatch.label_source = Dispatch.LabelSource.CHANNEL
    dispatch.label_sha256 = hashlib.sha256(label["pdf"]).hexdigest()
    dispatch.label_fetched_at = timezone.now()
    dispatch.save()
    return dispatch


def parse_packages(data):
    """Cajas del envío que manda el panel: una lista de filas `{"weight",
    "length", "width", "height", "quantity"}` en kg y cm; `quantity` (por
    defecto 1) son cajas iguales con esas medidas. Todas van en **una sola
    guía** (envío multipieza, decisión del usuario del 2026-10-19). Acepta
    también una sola fila sin lista (el panel anterior). Sin datos,
    `[DEFAULT_PACKAGE]`."""
    if not data:
        return [dict(DEFAULT_PACKAGE)]
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PACKAGE_ROWS:
        raise DispatchActionError(f"El envío debe traer entre 1 y {MAX_PACKAGE_ROWS} filas de paquetes.")
    return [_parse_package(row, index) for index, row in enumerate(rows, start=1)]


def _parse_package(data, index):
    """Una fila: cada medida, un número mayor que 0 (sin tope: lo que se
    envía es pequeño); la cantidad, un entero de 1 a `MAX_PACKAGE_QUANTITY`."""
    where = f" (paquete {index})" if index > 1 else ""
    if not isinstance(data, dict):
        raise DispatchActionError(f"El paquete debe traer peso, largo, ancho y alto{where}.")
    package = {}
    for field, label in PACKAGE_FIELDS.items():
        value = data.get(field)
        try:
            number = None if isinstance(value, bool) else Decimal(str(value).strip().replace(",", "."))
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite() or number <= 0:
            raise DispatchActionError(f"{label} debe ser un número mayor que 0{where}.")
        package[field] = float(number)
    quantity = str(data.get("quantity", 1)).strip()
    if isinstance(data.get("quantity"), bool) or not re.fullmatch(r"\d{1,3}", quantity) or not (
        1 <= int(quantity) <= MAX_PACKAGE_QUANTITY
    ):
        raise DispatchActionError(f"La cantidad debe ser un número entero de 1 a {MAX_PACKAGE_QUANTITY}{where}.")
    package["quantity"] = int(quantity)
    return package



def quote_dispatch_label(dispatch, packages=None):
    """Botón "Generar guía", paso 1 (no cobra): opciones de Envía Shipping
    desde la bodega del despacho hasta la dirección de envío del pedido,
    con los productos del despacho y las cajas del panel (`parse_packages`).
    Devuelve (opciones, ruta): la ruta es lo que se muestra para revisar
    antes de pagar, `{"origin": {name, city}, "destination": {name, address,
    city, region, phone}}`."""
    _check_generation(dispatch)
    order = dispatch.shopify_order
    shipping = _destination(order)
    route = {
        "origin": {"name": dispatch.location.name, "city": dispatch.location.city},
        "destination": {
            "name": " ".join(part for part in (shipping["first_name"], shipping["last_name"]) if part),
            "address": " ".join(part for part in (shipping["address1"], shipping["address2"]) if part),
            "city": shipping["city"],
            "region": shipping.get("province") or shipping["province_code"],
            "phone": shipping["phone"] or order.phone,
        },
    }
    return quote(_shipping_payload(dispatch, shipping, packages)), route


def generate_dispatch_label(dispatch, *, carrier, service, packages=None):
    """Botón "Generar guía", paso 2 (**cobra**): genera la guía de la opción
    elegida con las cajas cotizadas (una sola guía aunque sean varias), la
    baja y la deja en el despacho, con las cajas en `label_package`. Nunca
    genera una segunda. Si el resultado es incierto, lo anota en el
    despacho y no reintenta."""
    packages = packages or [dict(DEFAULT_PACKAGE)]
    _check_generation(dispatch)
    option = {"providerPayload": {"carrier": carrier, "service": service, "type": 1}}
    reference = label_reference(dispatch)
    try:
        result = create_label(_shipping_payload(dispatch, packages=packages), option, order_reference=reference)
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
    dispatch.label_package = packages
    dispatch.label_sha256 = hashlib.sha256(pdf).hexdigest()
    dispatch.label_pdf = pdf
    dispatch.label_fetched_at = timezone.now()
    dispatch.note = ""
    dispatch.save()
    return dispatch


def upload_dispatch_label(dispatch, pdf, *, tracking_number, carrier):
    """Botón "Cargar guía" (tienda web, Addi, cotizaciones y Madecentro):
    deja en el despacho una guía hecha fuera del sistema (p. ej. en el panel
    de Envía o con la transportadora), con su número y transportadora (la
    que se pone en Shopify al cumplir). Mismas reglas que "Generar guía": no
    reemplaza una guía que ya tenga (se libera primero). El PDF, hasta 5 MB
    como la carga de Sodimac."""
    _check_generation(dispatch)
    tracking_number = str(tracking_number or "").strip()
    carrier = str(carrier or "").strip()
    if not tracking_number or not carrier:
        raise DispatchActionError("Faltan el número de guía y la transportadora.")
    if len(tracking_number) > 64 or len(carrier) > 40:
        raise DispatchActionError("El número de guía o la transportadora son demasiado largos.")
    if not pdf or len(pdf) > MAX_PDF_BYTES or not bytes(pdf[:5]) == b"%PDF-":
        raise DispatchActionError("El archivo de la guía no es un PDF válido (o pesa más de 5 MB).")
    dispatch.tracking_number = tracking_number
    dispatch.label_source = Dispatch.LabelSource.MANUAL
    dispatch.label_url = ""
    dispatch.label_carrier = carrier
    dispatch.label_service = ""
    dispatch.label_package = []
    dispatch.label_sha256 = hashlib.sha256(pdf).hexdigest()
    dispatch.label_pdf = pdf
    dispatch.label_fetched_at = timezone.now()
    dispatch.note = ""
    dispatch.save()
    return dispatch


def release_envia_label(dispatch):
    """Botón "Liberar guía": quita del despacho la guía generada en Envía o
    cargada a mano para poder generar o cargar otra (otra transportadora u
    otro paquete). **No la cancela en Envía** (decisión del usuario,
    2026-10-19): se cancela a mano en el panel de Envía; por eso la guía
    liberada queda en `note`. Solo antes de avisar a la bodega y de cumplir
    en Shopify: después la guía ya salió."""
    if dispatch.label_source not in STORED_LABEL_SOURCES:
        raise DispatchActionError("El despacho no tiene guía generada en Envía ni cargada a mano.")
    if dispatch.notifications.exists() or dispatch.shopify_fulfilled_at:
        raise DispatchActionError(
            f"La guía {dispatch.tracking_number} ya se avisó a la bodega o está en Shopify: no se puede liberar."
        )
    released = f"{dispatch.tracking_number} ({' '.join(filter(None, (dispatch.label_carrier, dispatch.label_service)))})"
    cancel = (
        "cancelarla en Envía para que no se cobre" if dispatch.label_source == Dispatch.LabelSource.ENVIA
        else "anularla donde se hizo si no se va a usar"
    )
    dispatch.tracking_number = ""
    dispatch.label_source = ""
    dispatch.label_url = ""
    dispatch.label_carrier = ""
    dispatch.label_service = ""
    dispatch.label_package = []
    dispatch.label_sha256 = ""
    dispatch.label_pdf = None
    dispatch.label_fetched_at = None
    if dispatch.status == Dispatch.Status.READY:
        dispatch.status = Dispatch.Status.WAITING_LABEL
    dispatch.note = (
        f"Guía {released} liberada el {timezone.localdate():%Y-%m-%d}: {cancel}."
    )
    dispatch.save()
    return dispatch


def label_reference(dispatch):
    """Referencia de la guía en Envía: `pamo-<pedido>`; en un despacho de
    una bodega de un pedido repartido, `pamo-<pedido>-<despacho>`, para que
    cada bodega tenga la suya."""
    reference = f"pamo-{str(dispatch.shopify_order.name).lstrip('#')}"
    return f"{reference}-{dispatch.pk}" if dispatch.split else reference


def envia_label_file(dispatch):
    """PDF de la guía generada en Envía o cargada a mano del despacho:
    (nombre de archivo, bytes). Una guía generada antes de guardar el PDF se
    baja de su URL y queda guardada."""
    if dispatch.label_source not in STORED_LABEL_SOURCES:
        raise DispatchActionError("El despacho no tiene guía generada en Envía ni cargada a mano.")
    return f"{label_reference(dispatch)}-guia-{dispatch.tracking_number}.pdf", stored_envia_label(dispatch)


def _require_location(dispatch):
    reopen_manual_dispatch(dispatch)
    if dispatch.location is None:
        raise DispatchActionError(dispatch.note or "El pedido no tiene bodega asignada.")


def _check_generation(dispatch):
    order = dispatch.shopify_order
    _require_location(dispatch)
    if order.marketplace in CHANNEL_LABELS:
        raise DispatchActionError("Este canal trae su propia guía: usar \"Traer guía\".")
    if dispatch.location.creates_own_label and order.marketplace == SHOPIFY_CHANNEL:
        raise DispatchActionError(f"{dispatch.location.name} crea su propia guía: no se genera en Envía.")
    if dispatch.label_source in STORED_LABEL_SOURCES:
        raise DispatchActionError(
            f"Ya tiene una guía ({dispatch.tracking_number}); para cambiarla, liberarla primero."
        )


def _shipping_payload(dispatch, shipping=None, packages=None):
    """Envío de la guía: desde la bodega del despacho, con sus productos, en
    las cajas del panel (`parse_packages`; por defecto una `DEFAULT_PACKAGE`).
    Cada fila es un paquete de Envía con `amount` = cantidad; el valor
    declarado del despacho se reparte por igual entre todas las cajas."""
    packages = packages or [DEFAULT_PACKAGE]
    order = dispatch.shopify_order
    location = dispatch.location
    if not location.origin_address or not location.origin_phone:
        raise DispatchActionError(f"A {location.name} le falta dirección o teléfono en Shopify (sincronizar bodegas).")
    shipping = shipping or _destination(order)
    origin_city = resolve_colombia_city(location.city, location.origin_province_code)
    destination_city = resolve_colombia_city(shipping["city"], shipping["province_code"])
    lines = dispatch_lines(dispatch)
    names = ", ".join(line["name"] for line in lines if line["name"])
    declared = Decimal(str((lines_total(lines) if dispatch.split else order.total) or 0))
    per_box = float((declared / sum(package["quantity"] for package in packages)).quantize(Decimal("0.01")))
    return {
        "origin": {
            "name": location.name, "company": "Pamo", "phone": location.origin_phone, "email": DISPATCH_EMAIL,
            "street": location.origin_address, "city": origin_city["city"], "state": origin_city["state"],
            "country": "CO", "postalCode": location.origin_zip,
        },
        "destination": {
            "name": " ".join(part for part in (shipping["first_name"], shipping["last_name"]) if part) or "-",
            "company": shipping["company"], "phone": shipping["phone"] or order.phone,
            "email": shipping.get("email") or order.email,
            "street": " ".join(part for part in (shipping["address1"], shipping["address2"]) if part),
            "city": destination_city["city"], "state": destination_city["state"], "country": "CO",
            "postalCode": shipping["zip"], "identificationNumber": shipping.get("identification", ""),
        },
        "packages": [
            {
                "type": "box",
                # Servientrega rechaza más de 50 caracteres (`Des_DiceContener`).
                "content": (names or f"Pedido {order.name}")[:50],
                "amount": package["quantity"],
                "declaredValue": per_box,
                "weight": package["weight"],
                "dimensions": {dimension: package[dimension] for dimension in ("length", "width", "height")},
            }
            for package in packages
        ],
    }


def _destination(order):
    """Destino de la guía. Tienda web: la dirección de envío del pedido en
    Shopify (la cédula, de la copia local del pedido). Marketplace
    (Madecentro): la del comprador guardada en `MarketplaceOrder`, con su
    cédula, porque en Shopify el pedido va al cliente fijo del canal, sin
    dirección de envío."""
    if order.marketplace == SHOPIFY_CHANNEL:
        shipping = get_order_shipping_address(order.shopify_id)
        if not shipping or not shipping["address1"] or not shipping["city"]:
            raise DispatchActionError("El pedido no tiene dirección de envío en Shopify.")
        return {**shipping, "identification": order.customer_identification}
    row = MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id").first()
    if row is None or not row.customer_address or not row.customer_city:
        raise DispatchActionError("El pedido no tiene dirección de entrega del comprador.")
    return {
        "first_name": row.customer_first_name, "last_name": row.customer_last_name, "company": "",
        "email": row.customer_email, "phone": row.customer_phone, "address1": row.customer_address, "address2": "",
        "city": row.customer_city, "province_code": row.customer_region, "zip": "",
        "identification": row.customer_identification,
    }
