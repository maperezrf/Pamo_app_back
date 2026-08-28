"""Reglas comerciales locales trazables; nunca escriben en canales externos."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


D = Decimal
DRIVE_SOURCE = {
    "spreadsheet_id": "1EM6UVt379bIq7O96QpSe4QC8avIrqfDyYdt2bzgX4d4",
    "spreadsheet_title": "INFORME DE PRECIOS",
    "status": "REVIEWED_HYPOTHESIS",
}

MERCADO_PAGO_CO_SOURCE = {
    "label": "Mercado Pago Colombia · disponibilidad inmediata",
    "url": "https://www.mercadopago.com.co/herramientas-para-vender/check-out",
    "status": "PUBLIC_RATE_PENDING_ACCOUNT_VALIDATION",
    "observed_on": "2026-08-27",
}

DRIVE_COST_RULES = {
    "SHOPIFY": {
        # Diana registró 4% como aproximación de Mercado Pago. Para la
        # simulación local se usa la tarifa pública exacta de disponibilidad
        # inmediata y no se suma nuevamente la intermediación histórica 5%.
        "commission_percent": D("3.29"),
        "commission_fixed": D("800"),
        "commission_tax_percent": D("19"),
        "commission_label": "Mercado Pago",
        "payment_percent": D("0"),
        "administrative_percent": D("23"),
        "operating_fixed": D("7200"),
        "operating_label": "Alistamiento y bodegaje",
        "additional_fixed": D("360"),
        "additional_label": "Provisión de devolución (5% del alistamiento)",
        "target_net_margin_percent": D("20"),
        "logistics_reserve_percent": D("4"),
        "logistics_reserve_cap": D("40000"),
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


def _commission_amount(price, rule):
    percentage = price * rule["commission_percent"] / D("100")
    fixed = rule.get("commission_fixed", D("0"))
    tax_multiplier = D("1") + rule.get("commission_tax_percent", D("0")) / D("100")
    return _money((percentage + fixed) * tax_multiplier)


def _shopify_pricing_simulation(cost, current_price, rule):
    product_cost = _decimal(cost)
    sale_price = _decimal(current_price)
    target = rule["target_net_margin_percent"] / D("100")
    reserve_rate = rule["logistics_reserve_percent"] / D("100")
    reserve_cap = rule["logistics_reserve_cap"]
    if product_cost is None or product_cost <= 0:
        return {
            "status": "MISSING_PRODUCT_COST",
            "target_net_margin_percent": str(rule["target_net_margin_percent"]),
            "logistics_reserve_percent": str(rule["logistics_reserve_percent"]),
            "logistics_reserve_cap": str(reserve_cap),
            "external_writes": 0,
        }

    commission_rate_after_tax = (
        rule["commission_percent"] / D("100")
        * (D("1") + rule["commission_tax_percent"] / D("100"))
    )
    commission_fixed_after_tax = (
        rule["commission_fixed"]
        * (D("1") + rule["commission_tax_percent"] / D("100"))
    )
    admin_rate = rule["administrative_percent"] / D("100")
    internal_fixed = rule["operating_fixed"] + rule["additional_fixed"]
    variable_denominator = D("1") - commission_rate_after_tax - admin_rate - target
    uncapped_denominator = variable_denominator - reserve_rate
    if uncapped_denominator <= 0 or variable_denominator <= 0:
        return {
            "status": "INVALID_RULE",
            "external_writes": 0,
        }

    uncapped_price = (
        product_cost + internal_fixed + commission_fixed_after_tax
    ) / uncapped_denominator
    if uncapped_price * reserve_rate <= reserve_cap:
        suggested_price = uncapped_price
        logistics_reserve = suggested_price * reserve_rate
        reserve_basis = "PERCENT"
    else:
        suggested_price = (
            product_cost
            + internal_fixed
            + commission_fixed_after_tax
            + reserve_cap
        ) / variable_denominator
        logistics_reserve = reserve_cap
        reserve_basis = "CAPPED"

    suggested_price = _money(suggested_price)
    logistics_reserve = _money(min(logistics_reserve, reserve_cap))
    commission = _commission_amount(suggested_price, rule)
    administrative = _percent_amount(
        suggested_price, rule["administrative_percent"]
    )
    net_profit = _money(
        suggested_price
        - product_cost
        - commission
        - administrative
        - internal_fixed
        - logistics_reserve
    )
    achieved_margin = (
        net_profit / suggested_price * D("100")
        if suggested_price
        else None
    )
    markup = (
        (suggested_price / product_cost - D("1")) * D("100")
        if product_cost
        else None
    )
    difference = suggested_price - sale_price if sale_price is not None else None
    return {
        "status": "SIMULATED_LOCAL",
        "product_cost": str(_money(product_cost)),
        "current_price": str(_money(sale_price)) if sale_price is not None else None,
        "suggested_price": str(suggested_price),
        "difference_amount": str(_money(difference)) if difference is not None else None,
        "markup_percent": str(markup.quantize(D("0.1"), rounding=ROUND_HALF_UP)),
        "target_net_margin_percent": str(rule["target_net_margin_percent"]),
        "achieved_net_margin_percent": str(
            achieved_margin.quantize(D("0.1"), rounding=ROUND_HALF_UP)
        ) if achieved_margin is not None else None,
        "estimated_net_profit": str(net_profit),
        "logistics_reserve_percent": str(rule["logistics_reserve_percent"]),
        "logistics_reserve_cap": str(reserve_cap),
        "logistics_reserve_amount": str(logistics_reserve),
        "logistics_reserve_basis": reserve_basis,
        "mercado_pago_amount": str(commission),
        "administrative_amount": str(administrative),
        "internal_fixed_amount": str(_money(internal_fixed)),
        "formula_basis": "MARGIN_ON_SALE_AFTER_KNOWN_COSTS",
        "external_writes": 0,
    }


def enrich_commercial_payload(channel, price, payload=None, cost=None):
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
    channel_code = str(channel or "").upper()
    if channel_code == "SHOPIFY":
        commission_amount = _commission_amount(sale_price, rule)
        commission_percent = rule["commission_percent"]
        commission_basis = "MERCADO_PAGO_CO_PUBLIC_IMMEDIATE_RATE"
    elif api_commission is not None:
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
        "commission_label": rule.get("commission_label", "Comisión"),
        "commission_formula_label": (
            f"{rule['commission_percent']}% + ${int(rule.get('commission_fixed', 0)):,} + IVA"
            .replace(",", ".")
            if channel_code == "SHOPIFY"
            else None
        ),
        "other_cost_amount": str(other_cost_amount),
        "other_cost_labels": labels,
        "cost_breakdown": {
            "payment_amount": str(payment_amount),
            "administrative_amount": str(administrative_amount),
            "operating_amount": str(operating_amount),
            "operating_label": rule["operating_label"],
            "additional_amount": str(additional_amount),
        },
        "target_label": existing.get("target_label") or (
            "20%" if channel_code == "SHOPIFY" else "20–25%"
        ),
        "target_value": existing.get("target_value") or (
            str(rule["target_net_margin_percent"])
            if channel_code == "SHOPIFY"
            else None
        ),
        "pricing_simulation": (
            _shopify_pricing_simulation(cost, sale_price, rule)
            if channel_code == "SHOPIFY"
            else None
        ),
        "source": {
            "commission": (
                MERCADO_PAGO_CO_SOURCE
                if channel_code == "SHOPIFY"
                else "Mercado Libre API" if api_commission is not None else "INFORME DE PRECIOS"
            ),
            "internal_costs": DRIVE_SOURCE,
        },
    }
    return payload
