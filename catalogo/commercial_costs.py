"""Reglas comerciales locales trazables; nunca escriben en canales externos."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


D = Decimal
DRIVE_SOURCE = {
    "spreadsheet_id": "1EM6UVt379bIq7O96QpSe4QC8avIrqfDyYdt2bzgX4d4",
    "spreadsheet_title": "INFORME DE PRECIOS",
    "status": "REVIEWED_HYPOTHESIS",
}

DRIVE_COST_RULES = {
    "SHOPIFY": {
        "commission_percent": D("4"),
        "payment_percent": D("5"),
        "administrative_percent": D("23"),
        "operating_fixed": D("7200"),
        "operating_label": "Alistamiento y bodegaje",
        "additional_fixed": D("360"),
        "additional_label": "Provisión de devolución (5% del alistamiento)",
    },
    "MERCADO_LIBRE": {
        "commission_percent": D("14"),
        # Las ventas hechas dentro de Mercado Libre no generan una pasarela
        # adicional de Mercado Pago: el cargo aplicable viene en sale_fee.
        "payment_percent": D("0"),
        "administrative_percent": D("23"),
        "operating_fixed": D("7200"),
        "operating_label": "Alistamiento y bodegaje",
        "additional_fixed": D("1152"),
        "additional_label": "Provisión de devolución (16% del alistamiento)",
    },
    "FALABELLA": {
        "commission_percent": D("34"),
        "payment_percent": D("0"),
        "administrative_percent": D("20"),
        "operating_fixed": D("7200"),
        "operating_label": "Alistamiento y bodegaje",
        "additional_fixed": D("360"),
        "additional_label": "Provisión de devolución (5% del alistamiento)",
    },
    "SODIMAC": {
        "commission_percent": D("0"),
        "payment_percent": D("0"),
        "administrative_percent": D("22"),
        "operating_fixed": D("5000"),
        "operating_label": "Transporte estimado",
        "additional_fixed": D("0"),
        "additional_label": "",
    },
}


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value):
    return D(value).quantize(D("1"), rounding=ROUND_HALF_UP)


def _percent_amount(price, percent):
    return _money(price * D(percent) / D("100"))


def enrich_commercial_payload(channel, price, payload=None):
    """Combina tarifas del canal con hipótesis internas revisadas del Drive."""
    payload = dict(payload or {})
    rule = DRIVE_COST_RULES.get(str(channel or "").upper())
    sale_price = _decimal(price)
    if not rule or sale_price is None or sale_price <= 0:
        return payload

    existing = dict(payload.get("profitability") or {})
    selling_fees = dict(payload.get("selling_fees") or {})
    api_commission = _decimal(selling_fees.get("sale_fee_amount"))
    existing_commission = _decimal(existing.get("commission_amount"))
    if api_commission is not None:
        commission_amount = _money(api_commission)
        commission_percent = _decimal(selling_fees.get("percentage_fee"))
        commission_basis = "MELI_LISTING_PRICES_CURRENT_ITEM"
    elif existing_commission is not None:
        commission_amount = _money(existing_commission)
        commission_percent = _decimal(existing.get("commission_percent"))
        commission_basis = existing.get("basis") or "CHANNEL_PAYLOAD"
    else:
        commission_percent = rule["commission_percent"]
        commission_amount = _percent_amount(sale_price, commission_percent)
        commission_basis = "DRIVE_HISTORICAL_HYPOTHESIS"

    payment_amount = _percent_amount(sale_price, rule["payment_percent"])
    administrative_amount = _percent_amount(sale_price, rule["administrative_percent"])
    operating_amount = _money(rule["operating_fixed"])
    additional_amount = _money(rule["additional_fixed"])
    other_cost_amount = payment_amount + administrative_amount + operating_amount + additional_amount
    labels = []
    if rule["payment_percent"]:
        labels.append(f"Pasarela {rule['payment_percent']}%")
    labels.extend([
        f"Administración {rule['administrative_percent']}%",
        f"{rule['operating_label']} ${int(operating_amount):,}".replace(",", "."),
    ])
    if additional_amount:
        labels.append(rule["additional_label"])

    payload["profitability"] = {
        **existing,
        "verified": False,
        "commission_amount": str(commission_amount),
        "commission_percent": str(commission_percent) if commission_percent is not None else None,
        "commission_basis": commission_basis,
        "other_cost_amount": str(other_cost_amount),
        "other_cost_labels": labels,
        "cost_breakdown": {
            "payment_amount": str(payment_amount),
            "administrative_amount": str(administrative_amount),
            "operating_amount": str(operating_amount),
            "operating_label": rule["operating_label"],
            "additional_amount": str(additional_amount),
        },
        "target_label": existing.get("target_label") or "20–25%",
        "source": {
            "commission": "Mercado Libre API" if api_commission is not None else "INFORME DE PRECIOS",
            "internal_costs": DRIVE_SOURCE,
        },
    }
    return payload
