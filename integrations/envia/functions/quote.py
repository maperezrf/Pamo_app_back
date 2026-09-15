import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from config.constants import ENVIA_ALLOWED_CARRIERS

from ..client import EnviaClient
from .validate_shipping_payload import validate_shipping_payload

MAX_CARRIERS_PER_QUOTE = 12


class QuoteError(ValueError):
    pass


def _fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _delivery_days(raw):
    def days(value):
        text = str(value) if value is not None else ""
        return int(text) if re.fullmatch(r"\d{1,3}", text) else None

    minimum = days(raw.get("deliveryEstimateMin", raw.get("etaMinDays")))
    maximum = days(raw.get("deliveryEstimateMax", raw.get("etaMaxDays")))
    estimate = str(raw.get("deliveryEstimate") or "").strip()
    match = re.fullmatch(r"(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?(?:\s+(?:business\s+)?days?)?", estimate, re.I)
    if match and minimum is None and maximum is None:
        minimum, maximum = int(match[1]), int(match[2] or match[1])
    if minimum is not None and maximum is not None and minimum > maximum:
        return 0, 0
    return minimum or 0, maximum or 0


def quote(payload):
    """Cotiza un envío contra cada transportador permitido
    (ENVIA_ALLOWED_CARRIERS). Devuelve una lista de opciones; cada una
    trae `providerPayload`, que es lo que hay que pasarle después a
    create_label() para comprar exactamente esa opción.

    Lanza QuoteError si ningún transportador devolvió una tarifa elegible.
    """
    if not ENVIA_ALLOWED_CARRIERS:
        raise QuoteError("ENVIA_ALLOWED_CARRIERS_REQUIRED")
    validated = validate_shipping_payload(payload)
    client = EnviaClient()
    options = []
    for carrier in ENVIA_ALLOWED_CARRIERS[:MAX_CARRIERS_PER_QUOTE]:
        request_payload = {**validated, "shipment": {"type": 1, "carrier": carrier}}
        # Fiel al original: si un transportador falla, aborta toda la
        # cotización -- no sigue con los demás. Si prefieres que un
        # transportador con error no tumbe a los otros, lo cambiamos.
        result = client.post(client.shipping_base, "/ship/rate/", request_payload)
        raw_options = result.get("data") if isinstance(result.get("data"), list) else []
        for raw in raw_options:
            if not isinstance(raw, dict):
                continue
            try:
                price = Decimal(str(next(
                    (raw[key] for key in ("totalPrice", "price", "cost") if raw.get(key) is not None), None
                )))
            except (InvalidOperation, TypeError, ValueError):
                continue
            service = str(raw.get("service") or raw.get("serviceName") or "").strip()
            carrier_code = str(raw.get("carrier") or carrier).strip().lower()
            if not service or not price.is_finite() or price < 0 or price > Decimal("999999999"):
                continue
            eta_min, eta_max = _delivery_days(raw)
            option_identity = _fingerprint({"carrier": carrier_code, "service": service, "price": str(price)})
            options.append({
                "code": option_identity[:24],
                "carrier": carrier_code,
                "carrierLabel": str(raw.get("carrierDescription") or carrier_code)[:120],
                "service": service[:120],
                "price": float(price),
                "currency": str(raw.get("currency") or "COP")[:8].upper(),
                "etaMinDays": eta_min,
                "etaMaxDays": eta_max,
                "restrictions": [str(item)[:180] for item in (raw.get("restrictions") or [])[:8]],
                "providerPayload": {"carrier": carrier_code, "service": service[:120], "type": 1},
            })
    if not options:
        raise QuoteError("ENVIA_NO_ELIGIBLE_RATES")
    return options
