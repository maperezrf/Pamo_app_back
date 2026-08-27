import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import EnviaQuoteContractRun
from .physical import PhysicalValidationError, normalize_measurement


class EnviaQuoteContractError(ValueError):
    pass


def validate_quote_request(payload):
    destination = payload.get("destination") or {}
    package = payload.get("package") or {}
    for key in ("city", "state", "country"):
        if not str(destination.get(key) or "").strip():
            raise EnviaQuoteContractError(f"Falta destino.{key}.")
    normalized = {}
    for key, field in (("length", "LENGTH"), ("width", "WIDTH"), ("height", "HEIGHT"), ("weight", "WEIGHT")):
        if package.get(key) in (None, ""):
            raise EnviaQuoteContractError(f"Falta package.{key}.")
        try:
            value, unit = normalize_measurement(field, package[key], package.get(f"{key}_unit") or ("KG" if key == "weight" else "CM"))
        except PhysicalValidationError as error:
            raise EnviaQuoteContractError(str(error)) from error
        normalized[key] = str(value)
        normalized[f"{key}_unit"] = unit
    if package.get("evidence_classification") != "CONFIRMED" or package.get("scope") != "PACKAGE":
        raise EnviaQuoteContractError("La cotización exige medidas PACKAGE confirmadas.")
    evidence = package.get("evidence") or {}
    for field in ("length", "width", "height", "weight"):
        item = evidence.get(field) or {}
        if not item.get("candidate_id") or item.get("scope") != "PACKAGE" or item.get("classification") != "CONFIRMED" or item.get("decision") != "APPROVE_LOCAL":
            raise EnviaQuoteContractError(f"Falta evidencia aprobada y confirmada para package.{field}.")
    return {
        "destination": {
            "city": str(destination["city"]).strip(), "state": str(destination["state"]).strip(),
            "country": str(destination["country"]).strip(), "postal_code_prefix": str(destination.get("postal_code_prefix") or "")[:3],
        },
        "package": {**normalized, "scope": "PACKAGE", "evidence_classification": "CONFIRMED", "evidence": evidence},
        "currency": "COP", "purpose": "NON_BINDING_READ_ONLY_QUOTE",
    }


def validate_quote_response(payload):
    quotes = payload.get("quotes") or []
    if not quotes:
        raise EnviaQuoteContractError("La respuesta no contiene tarifas.")
    safe = []
    for quote in quotes:
        try:
            amount = Decimal(str(quote.get("amount")))
        except InvalidOperation as error:
            raise EnviaQuoteContractError("Tarifa no numérica.") from error
        if amount <= 0:
            raise EnviaQuoteContractError("Tarifa debe ser positiva; un error nunca equivale a envío gratis.")
        safe.append({
            "carrier": str(quote.get("carrier") or ""), "service": str(quote.get("service") or ""),
            "amount": str(amount), "currency": str(quote.get("currency") or "COP"),
            "estimated_days": int(quote.get("estimated_days") or 0),
        })
    return {"quotes": safe, "binding": False, "guide_created": False, "externalWrites": 0}


@transaction.atomic
def run_fixture_quote(request_payload, fixture_payload, fixture_name="envia_quote_fixture_v1.json"):
    request = validate_quote_request(request_payload)
    response = validate_quote_response(fixture_payload)
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    run, _ = EnviaQuoteContractRun.objects.update_or_create(
        request_fingerprint=fingerprint,
        defaults={
            "request_snapshot": request, "response_snapshot": response,
            "status": "FIXTURE_VALIDATED", "fixture_name": fixture_name, "external_writes": 0,
        },
    )
    return run
