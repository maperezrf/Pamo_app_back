import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from catalogo.envia_readiness import REQUIRED_PACKAGE_FIELDS
from catalogo.models import (
    IntegrationReadStatus,
    LogisticsQuoteSnapshot,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    ProductVariant,
)


DANE_CODE = re.compile(r"^\d{8}$")
PACKAGE_LIMITS = {
    "weight_kg": Decimal("1000"),
    "length_cm": Decimal("500"),
    "width_cm": Decimal("500"),
    "height_cm": Decimal("500"),
}


class ShippingPlanError(ValueError):
    pass


def _text(value, maximum):
    return str(value or "").strip()[:maximum]


def normalize_destination(value):
    value = value if isinstance(value, dict) else {}
    destination = {
        "address": _text(value.get("address"), 300),
        "city": _text(value.get("city"), 120),
        "department": _text(value.get("department"), 120),
        "dane_code": _text(value.get("dane_code"), 8),
        "postal_code": _text(value.get("postal_code"), 20),
        "country": _text(value.get("country") or "CO", 2).upper(),
    }
    if destination["country"] != "CO":
        raise ShippingPlanError("En esta fase el destino debe estar en Colombia.")
    if destination["dane_code"] and not DANE_CODE.fullmatch(destination["dane_code"]):
        raise ShippingPlanError("El código DANE debe tener 8 dígitos.")
    return destination


def normalize_package(value):
    value = value if isinstance(value, dict) else {}
    result = {
        "basis": "manual_confirmed",
        "confirmed": bool(value.get("confirmed")),
    }
    for field, maximum in PACKAGE_LIMITS.items():
        raw = value.get(field)
        if raw in (None, ""):
            result[field] = None
            continue
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ShippingPlanError(f"{field} debe ser un número válido.") from error
        if number <= 0 or number > maximum:
            raise ShippingPlanError(f"{field} debe ser mayor que cero y no superar {maximum}.")
        result[field] = str(number.normalize())
    return result


def _variant_lines(shipment):
    lines = []
    problems = []
    for shipment_item in shipment.shipment_items.all():
        sku = str(shipment_item.order_item.sku or "").strip()
        if not sku:
            problems.append("Un artículo no tiene SKU.")
            continue
        matches = list(
            ProductVariant.objects.filter(sku__iexact=sku)
            .exclude(product__status="STALE_LOCAL_SNAPSHOT")
            .select_related("product")[:2]
        )
        if not matches:
            problems.append(f"El SKU {sku} no existe en el catálogo local.")
            continue
        if len(matches) > 1:
            problems.append(f"El SKU {sku} está duplicado en el catálogo.")
            continue
        lines.append(
            {
                "variant": matches[0],
                "sku": sku,
                "name": shipment_item.order_item.name,
                "quantity": shipment_item.quantity,
            }
        )
    return lines, problems


def _approved_package(variant):
    now = timezone.now()
    result = {}
    candidates = (
        PhysicalEvidenceCandidate.objects.filter(
            variant=variant,
            scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
            classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
            conflict=False,
        )
        .prefetch_related("decisions")
        .order_by("field", "-confidence", "-observed_at")
    )
    for candidate in candidates:
        if candidate.field in result or (candidate.stale_after and candidate.stale_after < now):
            continue
        latest = next(iter(candidate.decisions.all()), None)
        if (
            not latest
            or latest.action != PhysicalEvidenceDecision.Action.APPROVE_LOCAL
            or (latest.expires_at and latest.expires_at < now)
        ):
            continue
        result[candidate.field] = {
            "value": str(candidate.normalized_value.normalize()),
            "unit": candidate.normalized_unit,
            "source": candidate.source_type,
        }
    return result


def _destination_matches(quote, destination):
    quoted = quote.destination if isinstance(quote.destination, dict) else {}
    quoted_city = str(quoted.get("city") or "").strip()
    quoted_department = str(quoted.get("state") or quoted.get("department") or "").strip()
    if destination.get("dane_code") and quoted_city == destination["dane_code"]:
        return True
    return bool(
        destination.get("city")
        and quoted_city.casefold() == destination["city"].casefold()
        and (
            not quoted_department
            or quoted_department.casefold() == destination.get("department", "").casefold()
        )
    )


def _route_quotes(lines, destination):
    if not lines or not destination.get("city") or not destination.get("department"):
        return []
    per_line = []
    for line in lines:
        matches = [
            quote
            for quote in LogisticsQuoteSnapshot.objects.filter(
                variant=line["variant"],
                provider="ENVIA",
                basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
                status=IntegrationReadStatus.Status.AVAILABLE,
                amount__isnull=False,
            ).order_by("-observed_at", "amount")
            if _destination_matches(quote, destination)
        ]
        if not matches:
            return []
        newest = max(item.observed_at for item in matches)
        current = [item for item in matches if item.observed_at == newest]
        by_carrier = {}
        for quote in current:
            carrier = (quote.carrier or "Sin transportadora").strip()
            selected = by_carrier.get(carrier)
            if not selected or quote.amount < selected.amount:
                by_carrier[carrier] = quote
        per_line.append((line, by_carrier))

    common = set.intersection(*(set(options) for _, options in per_line))
    options = []
    for carrier in sorted(common):
        quotes = [carrier_map[carrier] for _, carrier_map in per_line]
        amount = sum(
            quote.amount * Decimal(str(line["quantity"]))
            for (line, carrier_map), quote in zip(per_line, quotes)
        )
        observed_at = min(quote.observed_at for quote in quotes)
        raw = {
            "carrier": carrier,
            "service": next((quote.delivery_estimate for quote in quotes if quote.delivery_estimate), ""),
            "amount": str(amount),
            "currency": quotes[0].currency or "COP",
            "observed_at": observed_at.isoformat(),
            "basis": "ENVIA_CURRENT_ROUTE_QUOTE",
            "binding": False,
        }
        raw["fingerprint"] = hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode()
        ).hexdigest()
        options.append(raw)
    return sorted(options, key=lambda item: (Decimal(item["amount"]), item["carrier"]))


def _origin_ready(shipment, lines):
    if not shipment.warehouse_id or not lines:
        return False
    external_id = str(shipment.warehouse.external_id or "").strip()
    name = shipment.warehouse.name.strip().casefold()
    for line in lines:
        levels = line["variant"].inventory_levels.filter(
            location_active=True,
            address_verified=True,
        )
        if not levels.filter(location_external_id=external_id).exists() and not any(
            level.location_name.strip().casefold() == name for level in levels
        ):
            return False
    return True


def shipment_shipping_plan(shipment):
    lines, catalog_problems = _variant_lines(shipment)
    destination = normalize_destination(shipment.shipping_destination)
    manual_package = normalize_package(shipment.shipping_package)
    manual_package_ready = bool(
        manual_package.get("confirmed")
        and all(manual_package.get(field) for field in PACKAGE_LIMITS)
    )
    catalog_packages = []
    catalog_package_ready = bool(lines)
    for line in lines:
        package = _approved_package(line["variant"])
        ready = REQUIRED_PACKAGE_FIELDS <= set(package)
        catalog_package_ready = catalog_package_ready and ready
        catalog_packages.append(
            {
                "sku": line["sku"],
                "quantity": line["quantity"],
                "ready": ready,
                "fields": package,
            }
        )
    destination_ready = bool(
        destination.get("address")
        and destination.get("city")
        and destination.get("department")
        and DANE_CODE.fullmatch(destination.get("dane_code") or "")
    )
    origin_ready = _origin_ready(shipment, lines)
    package_ready = manual_package_ready or catalog_package_ready
    quote_options = _route_quotes(lines, destination) if destination_ready else []
    selection = shipment.shipping_quote_selection if isinstance(shipment.shipping_quote_selection, dict) else {}
    selected_option = next(
        (item for item in quote_options if item["fingerprint"] == selection.get("fingerprint")),
        None,
    )
    blockers = list(catalog_problems)
    if not shipment.warehouse_id:
        blockers.append("Asigna la bodega de este despacho.")
    elif not origin_ready:
        blockers.append("La bodega no tiene una dirección de origen verificada para todos los SKU.")
    if not destination_ready:
        blockers.append("Completa dirección, ciudad, departamento y código DANE del destino.")
    if not package_ready:
        blockers.append("Confirma peso y medidas del paquete o completa la evidencia PACKAGE del catálogo.")
    if destination_ready and package_ready and not quote_options:
        blockers.append("No hay una cotización vigente de esta ruta para todos los SKU.")
    if quote_options and not selected_option:
        blockers.append("Camila debe seleccionar una tarifa vigente.")
    if not shipment.order.customer_phone:
        blockers.append("El pedido no tiene teléfono de destinatario para la transportadora.")
    if hasattr(shipment, "document") or shipment.tracking_number:
        lifecycle = "created"
    elif selected_option:
        lifecycle = "selected"
    elif quote_options:
        lifecycle = "quoted"
    elif destination_ready and package_ready and origin_ready and not catalog_problems:
        lifecycle = "ready_to_quote"
    else:
        lifecycle = "not_ready"
    return {
        "shipmentId": str(shipment.id),
        "warehouse": shipment.effective_warehouse_name or None,
        "destination": destination,
        "destinationReady": destination_ready,
        "package": manual_package,
        "manualPackageReady": manual_package_ready,
        "catalogPackages": catalog_packages,
        "catalogPackageReady": catalog_package_ready,
        "packageReady": package_ready,
        "originReady": origin_ready,
        "quoteOptions": quote_options,
        "selectedQuote": selected_option,
        "guideRequestState": shipment.guide_request_state,
        "calculatedState": lifecycle,
        "readyToPrepare": not blockers and bool(selected_option),
        "blockers": blockers,
        "guideCreated": hasattr(shipment, "document") or bool(shipment.tracking_number),
        "externalWrites": 0,
        "bindingQuote": False,
    }


def select_quote(shipment, fingerprint):
    plan = shipment_shipping_plan(shipment)
    option = next(
        (item for item in plan["quoteOptions"] if item["fingerprint"] == fingerprint),
        None,
    )
    if not option:
        raise ShippingPlanError("La tarifa ya no está vigente; vuelve a cotizar.")
    return {
        **option,
        "selected_at": timezone.now().isoformat(),
        "selected_by_human": True,
    }
