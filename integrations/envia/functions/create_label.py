import re

import requests

from config.constants import ENVIA_ALLOWED_CARRIERS, ENVIA_WRITES_ENABLED

from ..client import EnviaAPIError, EnviaClient
from .validate_shipping_payload import validate_shipping_payload

REFERENCE_PATTERN = r"[A-Za-z0-9_-]{1,64}"


class LabelWritesDisabled(EnviaAPIError):
    """Generar una guía cobra: bloqueado sin `ENVIA_WRITES_ENABLED`."""

    def __init__(self):
        super().__init__("ENVIA_WRITES_DISABLED")


class LabelUnknownResult(EnviaAPIError):
    """La solicitud de guía salió y no se sabe si Envía la generó (y cobró):
    red, timeout, redirección, error o respuesta incompleta. **No
    reintentar**: revisar primero en Envía (historial de guías de la cuenta)
    si existe una con `order_reference`. Mismo criterio de `pamo-one`."""


def create_label(payload, selected_option, *, order_reference):
    """Genera (compra) la guía de una opción cotizada
    (`POST /ship/generate/`). Portado de `pamo-one-engineering`
    (`EnviaShippingClient.create_label`).

    - `payload`: el mismo de `quote` (origen, destino, paquetes); se vuelve
      a validar con `validate_shipping_payload`.
    - `selected_option`: una opción devuelta por `quote`; se usa su
      `providerPayload` (transportadora y servicio).
    - `order_reference`: referencia propia (ej. `"shopify-20412"`), que
      queda en la guía como `orderReference` y sirve para buscarla si el
      resultado es incierto.

    Antes de enviar, falla con `ValueError` / `EnviaAPIError` sin costo.
    Después de enviar, cualquier fallo es `LabelUnknownResult`.

    Devuelve {"tracking_number", "label_url", "shipment_id", "carrier",
    "service", "raw"}. El PDF se baja aparte con `download_label`.
    """
    validated = validate_shipping_payload(payload)
    if not re.fullmatch(REFERENCE_PATTERN, str(order_reference or "")):
        raise ValueError("order_reference inválida")
    provider_payload = selected_option.get("providerPayload") if isinstance(selected_option, dict) else None
    carrier = str((provider_payload or {}).get("carrier") or "").strip()
    service = str((provider_payload or {}).get("service") or "").strip()
    if not carrier or not service:
        raise ValueError("La opción elegida no trae transportadora y servicio")
    if carrier.lower() not in {allowed.lower() for allowed in ENVIA_ALLOWED_CARRIERS}:
        raise ValueError(f"Transportadora no permitida: {carrier}")
    if not ENVIA_WRITES_ENABLED:
        raise LabelWritesDisabled()

    client = EnviaClient()
    request_payload = {
        **validated,
        "shipment": {"type": 1, "carrier": carrier, "service": service, "orderReference": order_reference},
    }
    try:
        response = client.post(client.shipping_base, "/ship/generate/", request_payload)
    except (requests.RequestException, EnviaAPIError) as error:
        code = getattr(error, "code", type(error).__name__)
        raise LabelUnknownResult(f"ENVIA_LABEL_UNKNOWN_RESULT ({code})") from error

    data = response.get("data")
    if isinstance(data, list):
        if len(data) != 1:
            raise LabelUnknownResult("ENVIA_LABEL_RESPONSE_AMBIGUOUS")
        data = data[0]
    if not isinstance(data, dict):
        raise LabelUnknownResult("ENVIA_LABEL_RESPONSE_INCOMPLETE")
    tracking = _unique(data, "trackingNumber", "tracking_number")
    label_url = _unique(data, "label", "labelUrl", "label_url")
    if not tracking or not label_url:
        raise LabelUnknownResult("ENVIA_LABEL_RESPONSE_INCOMPLETE")
    return {
        "tracking_number": tracking,
        "label_url": label_url,
        "shipment_id": _unique(data, "shipmentId", "shipment_id") or tracking,
        "carrier": carrier,
        "service": service,
        "raw": data,
    }


def _unique(data, *names):
    # Si Envía manda el mismo dato con dos nombres distintos y valores
    # distintos, la respuesta es ambigua.
    values = {
        str(data[name]).strip()
        for name in names
        if isinstance(data.get(name), (str, int)) and not isinstance(data.get(name), bool) and str(data[name]).strip()
    }
    if len(values) > 1:
        raise LabelUnknownResult("ENVIA_LABEL_RESPONSE_AMBIGUOUS")
    return next(iter(values), "")
