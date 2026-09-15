import re
from decimal import Decimal, InvalidOperation


class ShippingPayloadError(ValueError):
    """El payload de envío no cumple lo que exige la API de Envía --
    portado de pamo-one-engineering, verificado ahí contra la API real."""


def _clean_text(value, maximum, code):
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ShippingPayloadError(code)
    return text


def _positive_decimal(value, code, *, allow_zero=False, integer=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ShippingPayloadError(code) from error
    if not result.is_finite() or (result < 0 if allow_zero else result <= 0) or result > Decimal("999999999"):
        raise ShippingPayloadError(code)
    if integer and result != result.to_integral_value():
        raise ShippingPayloadError(code)
    return int(result) if integer else float(result)


def _address(value, prefix):
    """`city`/`state` deben venir ya resueltos por
    integrations/envia/functions/resolve_colombia_city.py (código DANE),
    no un nombre de ciudad libre -- Envía exige el código, no el nombre,
    para destinos en Colombia."""
    if not isinstance(value, dict):
        raise ShippingPayloadError(f"ENVIA_{prefix}_ADDRESS_REQUIRED")
    country = _clean_text(value.get("country"), 2, f"ENVIA_{prefix}_COUNTRY_INVALID").upper()
    city = _clean_text(value.get("city"), 80, f"ENVIA_{prefix}_CITY_INVALID")
    if country == "CO" and not re.fullmatch(r"\d{8}", city):
        raise ShippingPayloadError(f"ENVIA_{prefix}_CITY_DANE_REQUIRED")
    phone = re.sub(r"[^+\d]", "", str(value.get("phone") or ""))
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        raise ShippingPayloadError(f"ENVIA_{prefix}_PHONE_INVALID")
    return {
        "name": _clean_text(value.get("name"), 120, f"ENVIA_{prefix}_NAME_INVALID"),
        "company": " ".join(str(value.get("company") or "").strip().split())[:120],
        "phone": phone,
        "email": " ".join(str(value.get("email") or "").strip().split())[:160],
        "street": _clean_text(value.get("street"), 240, f"ENVIA_{prefix}_STREET_INVALID"),
        "city": city,
        "state": _clean_text(value.get("state"), 80, f"ENVIA_{prefix}_STATE_INVALID"),
        "country": country,
        "postalCode": _clean_text(value.get("postalCode"), 16, f"ENVIA_{prefix}_POSTAL_INVALID"),
    }


def validate_shipping_payload(payload):
    """Valida y normaliza un payload de envío a la forma exacta que
    espera la API de Envía. Lo usan quote() y (más adelante) create_label()
    -- una sola fuente de verdad para esta validación."""
    if not isinstance(payload, dict):
        raise ShippingPayloadError("ENVIA_SHIPPING_PAYLOAD_INVALID")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list) or not 1 <= len(raw_packages) <= 20:
        raise ShippingPayloadError("ENVIA_PACKAGES_INVALID")
    packages = []
    for raw in raw_packages:
        if not isinstance(raw, dict) or not isinstance(raw.get("dimensions"), dict):
            raise ShippingPayloadError("ENVIA_PACKAGE_INVALID")
        packages.append({
            "type": _clean_text(raw.get("type") or "box", 40, "ENVIA_PACKAGE_TYPE_INVALID"),
            "content": _clean_text(raw.get("content"), 180, "ENVIA_PACKAGE_CONTENT_INVALID"),
            "amount": _positive_decimal(raw.get("amount"), "ENVIA_PACKAGE_AMOUNT_INVALID", integer=True),
            "declaredValue": _positive_decimal(
                raw.get("declaredValue", 0), "ENVIA_DECLARED_VALUE_INVALID", allow_zero=True
            ),
            "weight": _positive_decimal(raw.get("weight"), "ENVIA_WEIGHT_INVALID"),
            "weightUnit": "KG",
            "lengthUnit": "CM",
            "dimensions": {
                dimension: _positive_decimal(raw["dimensions"].get(dimension), f"ENVIA_{dimension.upper()}_INVALID")
                for dimension in ("length", "width", "height")
            },
        })
    return {
        "origin": _address(payload.get("origin"), "ORIGIN"),
        "destination": _address(payload.get("destination"), "DESTINATION"),
        "packages": packages,
        "settings": {
            "printFormat": str((payload.get("settings") or {}).get("printFormat") or "PDF"),
            "printSize": str((payload.get("settings") or {}).get("printSize") or "STOCK_4X6"),
        },
        "shipment": {"type": 1},
    }
