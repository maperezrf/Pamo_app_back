"""Reglas puras recuperadas de la línea base b7814b5.

Este módulo no realiza consultas ni escrituras externas. Mantiene las reglas
comerciales separadas de Django y de los adaptadores de Siigo para que puedan
probarse de forma determinista antes de conectar infraestructura.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation


DELIVERY_METHODS = {
    "PERSONAL_PICKUP",
    "CARRIER",
    "UBER",
    "INDRIVE",
    "COURIER",
    "OTHER",
}


class RemittanceDomainError(ValueError):
    def __init__(self, message, *, code="INVALID_REMITTANCE", details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def clean_text(value, field, *, required=False, max_length=500):
    if value is None or value == "":
        if required:
            raise RemittanceDomainError(f"{field} es obligatorio.")
        return None
    if not isinstance(value, str):
        raise RemittanceDomainError(f"{field} no es válido.")
    normalized = value.strip()
    if required and not normalized:
        raise RemittanceDomainError(f"{field} es obligatorio.")
    return normalized[:max_length]


def normalize_nit(value):
    return "".join(character for character in clean_text(value, "NIT", required=True, max_length=30) if character.isdigit())


def normalize_description(value, field="Descripción"):
    return clean_text(value, field, required=True, max_length=1000).upper()


def normalize_usage_destination(value):
    destination = clean_text(value, "Destino de uso", max_length=300)
    return destination.upper() if destination else None


def normalize_frequent_value(value, field="Nombre"):
    normalized = clean_text(value, field, required=True, max_length=200)
    return " ".join(normalized.split()).upper()


def _decimal(value, field, *, allow_none=True):
    if value is None or value == "":
        if allow_none:
            return None
        raise RemittanceDomainError(f"{field} es obligatorio.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise RemittanceDomainError(f"{field} no es válido.") from error
    if not amount.is_finite():
        raise RemittanceDomainError(f"{field} no es válido.")
    return amount


def normalize_money(value, field):
    amount = _decimal(value, field)
    if amount is None:
        return None
    if amount < 0:
        raise RemittanceDomainError(f"{field} no es válido.")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_percent(value, field):
    percent = _decimal(value, field)
    if percent is None:
        return None
    if percent < 0 or percent > 100:
        raise RemittanceDomainError(f"{field} no es válido.")
    return percent.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def normalize_supplier_profile(profile=None):
    profile = profile or {}
    margin_rate = _decimal(profile.get("margin_rate", "0.35"), "Margen", allow_none=False)
    tax_rate = _decimal(profile.get("tax_rate", "19"), "IVA del proveedor", allow_none=False)
    rounding_increment = _decimal(profile.get("rounding_increment", "100"), "Redondeo", allow_none=False)
    if margin_rate < 0 or margin_rate >= 1:
        raise RemittanceDomainError("El margen debe estar entre 0% y menos de 100%.")
    if tax_rate < 0 or tax_rate > 100:
        raise RemittanceDomainError("El IVA del proveedor no es válido.")
    if rounding_increment <= 0:
        raise RemittanceDomainError("El redondeo debe ser mayor que cero.")

    source_price_basis = clean_text(
        profile.get("source_price_basis", "AUTO"),
        "Origen del precio",
        required=True,
        max_length=20,
    )
    if source_price_basis not in {"AUTO", "UNIT", "LINE_TOTAL"}:
        raise RemittanceDomainError("El origen del precio no es válido.")

    product_type = clean_text(
        profile.get("siigo_product_type", "Product"),
        "Tipo de producto",
        required=True,
        max_length=30,
    )
    if product_type not in {"Product", "Service", "Consumer Good"}:
        raise RemittanceDomainError("El tipo de producto Siigo no es válido.")

    return {
        "margin_rate": margin_rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        "price_includes_tax": bool(profile.get("price_includes_tax", False)),
        "source_price_basis": source_price_basis,
        "tax_rate": tax_rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        "rounding_increment": rounding_increment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "allocate_global_discount": profile.get("allocate_global_discount", True) is not False,
        "include_other_charges_in_cost": profile.get("include_other_charges_in_cost", True) is not False,
        "include_freight_in_cost": profile.get("include_freight_in_cost", True) is not False,
        "siigo_account_group_id": profile.get("siigo_account_group_id") or None,
        "siigo_product_type": product_type,
        "siigo_unit_code": clean_text(
            profile.get("siigo_unit_code", "94"),
            "Unidad Siigo",
            required=True,
            max_length=20,
        ),
        "siigo_price_list_position": min(12, max(1, int(profile.get("siigo_price_list_position", 1) or 1))),
        "freight_siigo_product_id": clean_text(profile.get("freight_siigo_product_id"), "Producto de flete", max_length=100),
        "freight_siigo_code": clean_text(profile.get("freight_siigo_code"), "Código de flete", max_length=30),
        "freight_siigo_name": clean_text(profile.get("freight_siigo_name"), "Nombre de flete", max_length=100),
    }


def calculate_supplier_commercials(lines, profile=None, document=None):
    safe_profile = normalize_supplier_profile(profile)
    document = document or {}
    normalized = []
    for index, source in enumerate(lines, start=1):
        quantity = _decimal(source.get("quantity"), f"Cantidad de la línea {index}", allow_none=False)
        if quantity <= 0:
            raise RemittanceDomainError(f"Cantidad no válida en la línea {index}.")
        line_total = normalize_money(source.get("supplier_line_total"), f"Total proveedor de la línea {index}")
        explicit_unit = normalize_money(source.get("supplier_unit_cost"), f"Costo unitario de la línea {index}")

        if safe_profile["source_price_basis"] == "LINE_TOTAL":
            gross = line_total if line_total is not None else (explicit_unit * quantity if explicit_unit is not None else None)
        elif safe_profile["source_price_basis"] == "UNIT":
            gross = explicit_unit * quantity if explicit_unit is not None else line_total
        else:
            gross = line_total if line_total is not None else (explicit_unit * quantity if explicit_unit is not None else None)

        percent = normalize_percent(source.get("supplier_discount_percent"), f"Descuento de la línea {index}") or Decimal("0")
        value = normalize_money(source.get("supplier_discount_value"), f"Descuento de la línea {index}") or Decimal("0")
        discounted = None if gross is None else max(Decimal("0"), gross * (Decimal("1") - percent / Decimal("100")) - value)
        normalized.append({**source, "quantity": quantity, "gross": gross, "discounted": discounted})

    eligible = [line for line in normalized if line["discounted"] is not None]
    subtotal = sum((line["discounted"] for line in eligible), Decimal("0"))
    global_percent = normalize_percent(document.get("supplier_global_discount_percent"), "Descuento global") or Decimal("0")
    global_value = normalize_money(document.get("supplier_global_discount_value"), "Descuento global") or Decimal("0")
    other_charges = normalize_money(document.get("supplier_other_charges"), "Otros cargos") or Decimal("0")
    freight_cost = normalize_money(document.get("supplier_freight_cost"), "Flete") or Decimal("0")
    charges = (
        (other_charges if safe_profile["include_other_charges_in_cost"] else Decimal("0"))
        + (freight_cost if safe_profile["include_freight_in_cost"] else Decimal("0"))
    )

    result = []
    for line in normalized:
        if line["discounted"] is None:
            result.append({**line, "net_unit_cost": None, "suggested_invoice_unit_price": None})
            continue
        weight = line["discounted"] / subtotal if subtotal > 0 else Decimal("1") / max(len(eligible), 1)
        global_discount = (
            line["discounted"] * global_percent / Decimal("100") + global_value * weight
            if safe_profile["allocate_global_discount"]
            else Decimal("0")
        )
        net_line = max(Decimal("0"), line["discounted"] - global_discount + charges * weight)
        if safe_profile["price_includes_tax"] and safe_profile["tax_rate"] > 0:
            net_line /= Decimal("1") + safe_profile["tax_rate"] / Decimal("100")
        net_unit_cost = (net_line / line["quantity"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        raw_sale = net_unit_cost / (Decimal("1") - safe_profile["margin_rate"])
        increment = safe_profile["rounding_increment"]
        suggested = (raw_sale / increment).to_integral_value(rounding=ROUND_CEILING) * increment
        result.append({
            **line,
            "net_unit_cost": net_unit_cost,
            "suggested_invoice_unit_price": suggested.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        })
    return result


def calculate_margin_price(net_unit_cost, margin_percent, rounding_increment="100"):
    """Calcula precio de venta por margen bruto y redondea siempre hacia arriba."""
    cost = normalize_money(net_unit_cost, "Costo neto")
    margin = normalize_percent(margin_percent, "Margen")
    increment = _decimal(rounding_increment, "Redondeo", allow_none=False)
    if cost is None:
        return None
    if margin is None or margin >= 100:
        raise RemittanceDomainError(
            "El margen debe estar entre 0% y menos de 100%.",
            code="INVALID_MARGIN",
        )
    if increment <= 0:
        raise RemittanceDomainError("El redondeo debe ser mayor que cero.")
    raw_sale = cost / (Decimal("1") - margin / Decimal("100"))
    suggested = (raw_sale / increment).to_integral_value(rounding=ROUND_CEILING) * increment
    return suggested.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_siigo_invoice_price(
    net_unit_cost,
    margin_percent,
    *,
    tax_rate="0",
    tax_included=False,
    rounding_increment="100",
):
    """Calcula el precio base que se envía en ``items.price`` a Siigo.

    La API de facturas calcula el IVA desde ``items.taxes``. El indicador
    ``tax_included`` del catálogo no cambia el contrato de ``items.price``:
    siempre enviamos el precio antes de IVA y mostramos el total con IVA como
    un valor derivado.
    """
    cost = normalize_money(net_unit_cost, "Costo neto")
    rate = normalize_percent(tax_rate, "IVA") or Decimal("0")
    if cost is None:
        return None
    return calculate_margin_price(cost, margin_percent, rounding_increment)


def normalize_draft(payload):
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise RemittanceDomainError("Agrega al menos un producto.")
    if len(lines) > 200:
        raise RemittanceDomainError("La remisión supera 200 líneas.")
    method = clean_text(payload.get("delivery_method"), "Modalidad de salida", required=True, max_length=30)
    if method not in DELIVERY_METHODS:
        raise RemittanceDomainError("Modalidad de salida no válida.")

    normalized_lines = []
    for index, line in enumerate(lines, start=1):
        quantity = _decimal(line.get("quantity"), f"Cantidad de la línea {index}", allow_none=False)
        if quantity <= 0:
            raise RemittanceDomainError(f"Cantidad no válida en la línea {index}.")
        line_total = normalize_money(line.get("supplier_line_total"), f"Total proveedor de la línea {index}")
        explicit_unit = normalize_money(line.get("supplier_unit_cost"), f"Costo unitario de la línea {index}")
        supplier_unit_cost = explicit_unit
        if supplier_unit_cost is None and line_total is not None:
            supplier_unit_cost = (line_total / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        normalized_lines.append({
            "id": uuid.uuid4(),
            "line_number": index,
            "quantity": quantity,
            "description": normalize_description(line.get("description"), f"Descripción de la línea {index}"),
            "usage_destination": normalize_usage_destination(line.get("usage_destination")),
            "supplier_sku": (clean_text(line.get("supplier_sku"), "SKU proveedor", max_length=120) or "").upper() or None,
            "supplier_unit_cost": supplier_unit_cost,
            "supplier_line_total": line_total,
            "supplier_discount_percent": normalize_percent(line.get("supplier_discount_percent"), f"Descuento de la línea {index}"),
            "supplier_discount_value": normalize_money(line.get("supplier_discount_value"), f"Descuento de la línea {index}"),
        })

    return {
        "id": uuid.uuid4(),
        "warehouse_id": clean_text(payload.get("warehouse_id"), "Bodega", required=True, max_length=36),
        "supplier_party_id": clean_text(payload.get("supplier_party_id"), "Proveedor", required=True, max_length=36),
        "customer_party_id": clean_text(payload.get("customer_party_id"), "Cliente", required=True, max_length=36),
        "requester_name": normalize_frequent_value(payload.get("requester_name"), "Solicitante"),
        "requester_document": clean_text(payload.get("requester_document"), "Documento", max_length=60),
        "notes": clean_text(payload.get("notes"), "Notas", max_length=2000),
        "delivery_method": method,
        "lines": normalized_lines,
    }


def ensure_expected_version(record_version, expected_version):
    try:
        expected = int(expected_version)
    except (TypeError, ValueError) as error:
        raise RemittanceDomainError(
            "La versión esperada es obligatoria.",
            code="EXPECTED_VERSION_REQUIRED",
        ) from error
    if record_version != expected:
        raise RemittanceDomainError(
            "La remisión cambió en otra sesión. Recarga antes de continuar.",
            code="VERSION_CONFLICT",
            details={"current_version": record_version},
        )


def invoice_readiness(remittance):
    lines = remittance.get("lines") or []
    all_coded = bool(lines) and all(
        line.get("master_product_id")
        and line.get("invoice_description")
        and line.get("invoice_unit_price") is not None
        for line in lines
    )
    delivery_ready = (
        remittance.get("delivery_status") == "COMPLETED"
        or (remittance.get("delivery") or {}).get("method") == "PERSONAL_PICKUP"
    )
    return remittance.get("document_status") == "CONFIRMED" and delivery_ready and all_coded
