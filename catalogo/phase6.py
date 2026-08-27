"""Motores deterministas de Fase 6; no contienen conectores ni escrituras externas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
from itertools import product
import json


D = Decimal
ROUNDING_CHOICES = {100, 500, 1000}
PRICING_MODES = {"MARKUP_PERCENT", "FIXED_INCREMENT", "GROSS_MARGIN"}
FILTER_DIMENSIONS = (
    "sku", "channel", "warehouse", "provider", "brand", "collection",
    "category", "product_type", "tags",
)


def _decimal(value, *, default=None):
    if value in (None, ""):
        return default
    return D(str(value))


def _money(value):
    if value is None:
        return None
    return D(value).quantize(D("0.01"))


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def stable_fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_value)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _list(value):
    if value in (None, "", []):
        return []
    return value if isinstance(value, list) else [value]


def _normalized_strings(value):
    return {str(item).strip().casefold() for item in _list(value) if str(item).strip()}


def _date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# Las dimensiones se combinan con AND; las opciones dentro de cada dimensión, con OR.
def rule_match(rule, context, on_date=None):
    today = on_date or date.today()
    if not rule.get("active", True):
        return False, "Regla inactiva"
    valid_from, valid_until = _date(rule.get("valid_from")), _date(rule.get("valid_until"))
    if valid_from and today < valid_from:
        return False, f"Aún no vigente en Colombia ({valid_from.isoformat()})"
    if valid_until and today > valid_until:
        return False, f"Venció en Colombia ({valid_until.isoformat()})"
    filters = rule.get("filters") or {}
    for dimension in FILTER_DIMENSIONS:
        expected = _normalized_strings(filters.get(dimension))
        if not expected:
            continue
        actual = _normalized_strings(context.get(dimension))
        if not actual or expected.isdisjoint(actual):
            return False, f"No coincide {dimension}"
    numeric_filters = {
        "price_min": ("before_price", lambda actual, expected: actual >= expected),
        "price_max": ("before_price", lambda actual, expected: actual <= expected),
        "cost_min": ("canonical_cost", lambda actual, expected: actual >= expected),
        "cost_max": ("canonical_cost", lambda actual, expected: actual <= expected),
        "inventory_min": ("inventory", lambda actual, expected: actual >= expected),
        "inventory_max": ("inventory", lambda actual, expected: actual <= expected),
    }
    for key, (context_key, comparator) in numeric_filters.items():
        expected = _decimal(filters.get(key))
        if expected is None:
            continue
        actual = _decimal(context.get(context_key))
        if actual is None:
            return False, f"{context_key} es UNKNOWN"
        if not comparator(actual, expected):
            return False, f"No coincide {key}"
    return True, "Coincide con todos los filtros"


def rule_specificity(rule):
    filters = rule.get("filters") or {}
    if _list(filters.get("sku")):
        return 400, "SKU exacto"
    if _list(filters.get("channel")) and _list(filters.get("warehouse")):
        return 300, "Canal + bodega"
    if any(_list(filters.get(key)) for key in ("collection", "category", "product_type", "brand", "provider")):
        return 200, "Colección / categoría / marca / proveedor"
    return 100, "Global"


def resolve_rule(rules, context, on_date=None):
    considered = []
    matched = []
    for rule in rules:
        applies, reason = rule_match(rule, context, on_date=on_date)
        specificity, label = rule_specificity(rule)
        item = {
            "id": str(rule.get("id") or rule.get("name")),
            "name": rule.get("name") or "Regla sin nombre",
            "matched": applies,
            "reason": reason,
            "specificity": specificity,
            "precedence": label,
            "priority": int(rule.get("priority") or 0),
        }
        considered.append(item)
        if applies:
            matched.append((specificity, item["priority"], str(item["id"]), rule, item))
    matched.sort(key=lambda row: (-row[0], -row[1], row[2]))
    if not matched:
        return {"winner": None, "discarded": considered, "conflicts": [], "blocked": True}
    top_specificity, top_priority = matched[0][0], matched[0][1]
    ties = [row for row in matched if row[0] == top_specificity and row[1] == top_priority]
    signatures = {stable_fingerprint({"pricing": row[3].get("pricing") or {}, "exception": row[3].get("exception") or {}}) for row in ties}
    conflicts = [row[4] for row in ties] if len(ties) > 1 and len(signatures) > 1 else []
    winner = None if conflicts else matched[0][3]
    winner_id = str(winner.get("id") or winner.get("name")) if winner else None
    discarded = []
    for item in considered:
        if item["id"] == winner_id:
            continue
        if item["matched"]:
            item = {**item, "reason": f"Descartada por precedencia/prioridad frente a {winner_id}"}
        discarded.append(item)
    return {"winner": winner, "discarded": discarded, "conflicts": conflicts, "blocked": bool(conflicts)}


@dataclass(frozen=True)
class PricingResult:
    status: str
    payload: dict


# El costo se lleva una sola vez a base IVA incluido; Barú INCLUDED no recibe un segundo 19 %.
def normalize_cost(context):
    raw_cost = _decimal(context.get("canonical_cost"))
    treatment = str(context.get("tax_treatment") or "PENDING").upper()
    tax_rate = _decimal(context.get("tax_rate"), default=D("19"))
    if raw_cost is None or raw_cost <= 0:
        return None, ["COST_UNKNOWN"]
    if treatment == "PENDING":
        return None, ["TAX_TREATMENT_UNKNOWN"]
    if treatment == "INCLUDED":
        return raw_cost, []
    if treatment == "EXCLUDED":
        return raw_cost * (D("1") + tax_rate / D("100")), []
    return None, ["TAX_TREATMENT_INVALID"]


def _ceil_increment(value, increment):
    return (D(value) / D(increment)).to_integral_value(rounding=ROUND_CEILING) * D(increment)


# El piso protege margen sobre venta; el tope de reserva se valida pero jamás se suma como recargo.
def calculate_protected_price(context, rule_resolution):
    blockers, warnings = [], []
    if rule_resolution.get("conflicts"):
        blockers.append("RULE_CONFLICT")
    rule = rule_resolution.get("winner")
    if not rule:
        blockers.append("NO_WINNING_RULE")
        return PricingResult("BLOCKED", {
            "variant_id": context.get("variant_id"), "sku": context.get("sku"), "before_price": context.get("before_price"),
            "blockers": blockers, "warnings": warnings, "rule_resolution": rule_resolution,
        })
    pricing = rule.get("pricing") or {}
    mode = str(pricing.get("mode") or "").upper()
    if mode not in PRICING_MODES:
        blockers.append("PRICING_MODE_INVALID")
    cost, cost_blockers = normalize_cost(context)
    blockers.extend(cost_blockers)
    value = _decimal(pricing.get("value"))
    if value is None or value < 0:
        blockers.append("PRICING_VALUE_UNKNOWN")
    rounding = int(pricing.get("rounding_increment") or 100)
    if rounding not in ROUNDING_CHOICES:
        blockers.append("ROUNDING_INVALID")
    if blockers:
        return PricingResult("BLOCKED", {
            "variant_id": context.get("variant_id"), "sku": context.get("sku"), "before_price": context.get("before_price"),
            "cost_source": context.get("cost_source") or "UNKNOWN", "blockers": blockers,
            "warnings": warnings, "rule_resolution": rule_resolution,
        })

    if mode == "MARKUP_PERCENT":
        candidate = cost * (D("1") + value / D("100"))
        candidate_formula = f"costo IVA incluido × (1 + {value}% markup)"
    elif mode == "FIXED_INCREMENT":
        candidate = cost + value
        candidate_formula = f"costo IVA incluido + COP {value}"
    else:
        if value >= 100:
            return PricingResult("BLOCKED", {
                "variant_id": context.get("variant_id"), "sku": context.get("sku"), "blockers": ["GROSS_MARGIN_INVALID"],
                "warnings": warnings, "rule_resolution": rule_resolution,
            })
        candidate = cost / (D("1") - value / D("100"))
        candidate_formula = f"costo IVA incluido ÷ (1 - {value}% margen bruto)"

    commission_pct = _decimal(pricing.get("channel_commission_percent"), default=D("0"))
    payment_pct = _decimal(pricing.get("payment_percent"), default=D("0"))
    administrative_pct = _decimal(pricing.get("administrative_percent"), default=D("0"))
    logistics_pct = _decimal(pricing.get("logistics_percent"), default=D("0"))
    minimum_margin = _decimal(pricing.get("minimum_margin_percent"), default=D("0"))
    channel_fixed = _decimal(pricing.get("channel_fixed_charge"), default=D("0"))
    payment_fixed = _decimal(pricing.get("payment_fixed_charge"), default=D("0"))
    administrative_fixed = _decimal(pricing.get("administrative_fixed_charge"), default=D("0"))
    logistics_fixed = _decimal(pricing.get("logistics_fixed_charge"), default=D("0"))
    additional_sale_pct = D("0")
    additional_cost_pct = D("0")
    additional_fixed = D("0")
    additional_breakdown = []
    for index, component in enumerate(pricing.get("additional_costs") or []):
        label = str(component.get("label") or f"Costo adicional {index + 1}").strip()
        basis = str(component.get("basis") or "PERCENT_SALE").upper()
        component_value = _decimal(component.get("value"))
        if component_value is None or component_value < 0:
            blockers.append("ADDITIONAL_COST_INVALID")
            continue
        if basis == "PERCENT_SALE":
            additional_sale_pct += component_value
        elif basis == "PERCENT_COST":
            additional_cost_pct += component_value
        elif basis == "FIXED":
            additional_fixed += component_value
        else:
            blockers.append("ADDITIONAL_COST_BASIS_INVALID")
            continue
        additional_breakdown.append({"label": label[:120], "basis": basis, "value": _money(component_value)})
    subsidy = _decimal(context.get("shipping_subsidy_used"))
    if subsidy is None:
        blockers.append("SHIPPING_SUBSIDY_UNKNOWN")
        subsidy = D("0")
    if subsidy < 0:
        blockers.append("SHIPPING_SUBSIDY_INVALID")
    reserve_cap = _decimal(pricing.get("reserve_cap"), default=D("0"))
    if reserve_cap and subsidy > reserve_cap:
        blockers.append("SUBSIDY_EXCEEDS_RESERVE_CAP")
    percentage_fields = [commission_pct, payment_pct, administrative_pct, logistics_pct, additional_sale_pct, additional_cost_pct]
    if any(value < 0 or value > 100 for value in percentage_fields):
        blockers.append("COMMERCIAL_PERCENTAGE_INVALID")
    sale_cost_pct = commission_pct + payment_pct + administrative_pct + logistics_pct + additional_sale_pct
    cost_based_amount = cost * additional_cost_pct / D("100")
    fixed_costs = channel_fixed + payment_fixed + administrative_fixed + logistics_fixed + additional_fixed
    denominator = D("1") - (sale_cost_pct + minimum_margin) / D("100")
    if denominator <= 0:
        blockers.append("PROTECTED_FLOOR_DENOMINATOR_INVALID")
        denominator = D("1")
    protected_floor = (cost + cost_based_amount + fixed_costs + subsidy) / denominator
    unrounded = max(candidate, protected_floor)

    minimum_price = _decimal(pricing.get("minimum_price"))
    maximum_price = _decimal(pricing.get("maximum_price"))
    if minimum_price is not None:
        unrounded = max(unrounded, minimum_price)
    exception = rule.get("exception") or {}
    exception_status = str(exception.get("status") or "NONE").upper()
    if exception_status in {"BLOCKED", "PENDING"}:
        blockers.append(f"EXCEPTION_{exception_status}")
    if exception_status == "OVERRIDE":
        override = _decimal(exception.get("price"))
        if not exception.get("approved_local") or override is None:
            blockers.append("EXCEPTION_PENDING_APPROVAL")
        elif override < protected_floor:
            blockers.append("EXCEPTION_BELOW_PROTECTED_FLOOR")
        else:
            unrounded = override
            warnings.append("EXPLICIT_LOCAL_EXCEPTION_USED")
    final_price = _ceil_increment(unrounded, rounding)
    if maximum_price is not None and final_price > maximum_price:
        blockers.append("MAXIMUM_PRICE_BELOW_PROTECTED_RESULT")

    commission_amount = final_price * commission_pct / D("100") + channel_fixed
    payment_amount = final_price * payment_pct / D("100") + payment_fixed
    administrative_amount = final_price * administrative_pct / D("100") + administrative_fixed
    logistics_amount = final_price * logistics_pct / D("100") + logistics_fixed
    additional_sale_amount = final_price * additional_sale_pct / D("100")
    additional_amount = additional_sale_amount + cost_based_amount + additional_fixed
    profit = final_price - cost - commission_amount - payment_amount - administrative_amount - logistics_amount - additional_amount - subsidy
    achieved_margin = profit / final_price * D("100") if final_price else D("0")
    status = "BLOCKED" if blockers else "READY_LOCAL"
    return PricingResult(status, {
        "variant_id": context.get("variant_id"), "sku": context.get("sku"),
        "before_price": _money(_decimal(context.get("before_price"))),
        "candidate_price": _money(candidate),
        "protected_floor": _money(protected_floor),
        "final_price": None if blockers else _money(final_price),
        "display_final_price": _money(final_price),
        "cost_source": context.get("cost_source") or "UNKNOWN",
        "normalized_cost_iva_included": _money(cost),
        "tax_treatment": context.get("tax_treatment"),
        "achieved_margin_percent": achieved_margin.quantize(D("0.001")),
        "commission_amount": _money(commission_amount),
        "payment_amount": _money(payment_amount),
        "administrative_amount": _money(administrative_amount),
        "logistics_amount": _money(logistics_amount),
        "additional_cost_amount": _money(additional_amount),
        "additional_cost_breakdown": additional_breakdown,
        "shipping_subsidy_used": _money(subsidy),
        "reserve_cap": _money(reserve_cap),
        "reserve_added_to_price": D("0.00"),
        "breakdown": [
            {"order": 1, "label": "Costo canónico", "value": _money(cost)},
            {"order": 2, "label": "Precio candidato", "value": _money(candidate), "formula": candidate_formula},
            {"order": 3, "label": "Piso protegido", "value": _money(protected_floor), "formula": "(costo + cargos fijos + costos sobre costo + subsidio usado) ÷ (1 - comisión - pago - administración - logística - otros % venta - margen mínimo)"},
            {"order": 4, "label": "Mayor entre candidato y piso", "value": _money(max(candidate, protected_floor))},
            {"order": 5, "label": f"Redondeo hacia arriba COP {rounding}", "value": _money(final_price)},
        ],
        "blockers": blockers,
        "warnings": warnings,
        "rule_resolution": rule_resolution,
        "external_writes": 0,
    })


def preview_pricing(contexts, rules, on_date=None):
    rows = []
    for context in contexts:
        resolution = resolve_rule(rules, context, on_date=on_date)
        rows.append(calculate_protected_price(context, resolution).payload)
    ready = sum(not row.get("blockers") for row in rows)
    return {
        "status": "NO_RESULTS" if not rows else "READY_LOCAL" if ready == len(rows) else "BLOCKED",
        "rows": rows,
        "summary": {"total": len(rows), "ready": ready, "blocked": len(rows) - ready},
        "external_writes": 0,
    }


# Cada SKU permanece indivisible y solo usa orígenes con stock conocido, fresco y suficiente.
def _line_origins(line, now):
    quantity = _decimal(line.get("quantity"), default=D("0"))
    origins, blockers = [], []
    saw_stale = False
    for record in line.get("inventory") or []:
        if record.get("unknown", True) or record.get("available") is None:
            continue
        observed = record.get("observed_at")
        if isinstance(observed, str):
            observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        freshness = int(record.get("freshness_minutes") or 0)
        if not observed or freshness <= 0 or observed + timedelta(minutes=freshness) < now:
            saw_stale = True
            continue
        available = _decimal(record.get("available"), default=D("0"))
        if available >= quantity:
            origins.append(str(record.get("origin") or "UNKNOWN"))
    if not origins:
        if saw_stale:
            blockers.append("STALE_INVENTORY")
        elif not line.get("inventory") or all(row.get("unknown", True) or row.get("available") is None for row in line.get("inventory") or []):
            blockers.append("WAREHOUSE_INVENTORY_UNKNOWN")
        else:
            blockers.append("INSUFFICIENT_VERIFIED_STOCK")
    return sorted(set(origins)), blockers


def _fixture_quote(origin, assigned_lines):
    units = sum(int(_decimal(line.get("quantity"), default=D("0"))) for line in assigned_lines)
    origin_bias = D(int(sha256(origin.encode()).hexdigest()[:4], 16) % 4) * D("500")
    return D("11000") + D("1500") * len(assigned_lines) + D("650") * units + origin_bias


def _serialize_assignment(assignment, lines, customer_shipping_charge, minimum_margin):
    groups = {}
    for origin, line in zip(assignment, lines):
        groups.setdefault(origin, []).append(line)
    guides = []
    total_logistics = D("0")
    for origin in sorted(groups):
        cost = _fixture_quote(origin, groups[origin])
        total_logistics += cost
        guides.append({"origin": origin, "guide_count": 1, "status": "ESTIMATED_DEMO", "cost": _money(cost), "skus": [line["sku"] for line in groups[origin]]})
    sales = sum(_decimal(line.get("unit_price"), default=D("0")) * _decimal(line.get("quantity"), default=D("0")) for line in lines)
    costs = sum(_decimal(line.get("unit_cost"), default=D("0")) * _decimal(line.get("quantity"), default=D("0")) for line in lines)
    pre_profit = sales - costs
    subsidy = max(total_logistics - customer_shipping_charge, D("0"))
    post_profit = pre_profit - subsidy
    pre_margin = pre_profit / sales * D("100") if sales else D("0")
    post_margin = post_profit / sales * D("100") if sales else D("0")
    line_rows = []
    for origin, line in zip(assignment, lines):
        line_sale = _decimal(line.get("unit_price"), default=D("0")) * _decimal(line.get("quantity"), default=D("0"))
        line_cost = _decimal(line.get("unit_cost"), default=D("0")) * _decimal(line.get("quantity"), default=D("0"))
        allocated = subsidy * line_sale / sales if sales else D("0")
        line_rows.append({
            "sku": line["sku"], "origin": origin, "quantity": line.get("quantity"),
            "margin_before_logistics": ((line_sale - line_cost) / line_sale * D("100")).quantize(D("0.001")) if line_sale else D("0"),
            "margin_after_logistics": ((line_sale - line_cost - allocated) / line_sale * D("100")).quantize(D("0.001")) if line_sale else D("0"),
        })
    return {
        "assignment": [{"sku": line["sku"], "origin": origin, "quantity": line.get("quantity")} for origin, line in zip(assignment, lines)],
        "guides": guides, "guide_count": len(guides), "logistics_cost": _money(total_logistics),
        "customer_shipping_charge": _money(customer_shipping_charge), "shipping_subsidy": _money(subsidy),
        "sales": _money(sales), "costs": _money(costs), "profit_before_logistics": _money(pre_profit),
        "profit_after_logistics": _money(post_profit), "margin_before_logistics": pre_margin.quantize(D("0.001")),
        "margin_after_logistics": post_margin.quantize(D("0.001")), "lines": line_rows,
        "margin_protected": post_margin >= minimum_margin,
    }


# Exacto hasta ocho líneas; después usa una heurística determinista y documentada.
def simulate_multwarehouse_cart(lines, *, demo_fixture=False, customer_shipping_charge=0, minimum_margin_percent=0, now=None):
    now = now or datetime.now(timezone.utc)
    customer_charge = _decimal(customer_shipping_charge, default=D("0"))
    minimum_margin = _decimal(minimum_margin_percent, default=D("0"))
    blockers, origin_options = [], []
    # Consolida SKU repetidos antes de asignar origen para impedir que un carrito duplique y divida el mismo SKU.
    consolidated = {}
    for raw in lines:
        sku = str(raw.get("sku") or "").strip()
        if not sku or sku not in consolidated:
            consolidated[sku] = dict(raw)
            continue
        current = consolidated[sku]
        comparable = ("unit_price", "unit_cost", "package_ready", "inventory")
        if any(stable_fingerprint(current.get(field)) != stable_fingerprint(raw.get(field)) for field in comparable):
            blockers.append(f"DUPLICATE_SKU_CONTEXT_CONFLICT:{sku}")
            continue
        current["quantity"] = _decimal(current.get("quantity"), default=D("0")) + _decimal(raw.get("quantity"), default=D("0"))
    normalized_lines = []
    for raw in consolidated.values():
        line = dict(raw)
        quantity = _decimal(line.get("quantity"))
        if not line.get("sku") or quantity is None or quantity <= 0:
            blockers.append("INVALID_CART_LINE")
            continue
        if _decimal(line.get("unit_price")) is None or _decimal(line.get("unit_cost")) is None:
            blockers.append(f"PRICE_OR_COST_UNKNOWN:{line.get('sku')}")
        origins, stock_blockers = _line_origins(line, now)
        blockers.extend(f"{code}:{line.get('sku')}" for code in stock_blockers)
        if not line.get("package_ready", False) and not demo_fixture:
            blockers.append(f"NO_COTIZABLE_PACKAGE:{line.get('sku')}")
        origin_options.append(origins)
        normalized_lines.append(line)
    if blockers or not normalized_lines:
        return {
            "status": "NO_COTIZABLE", "quote_basis": "NO_CURRENT_RATE",
            "blockers": sorted(set(blockers or ["EMPTY_CART"])), "strategies": [],
            "recommended_strategy": None, "commercially_eligible": False, "external_writes": 0,
        }

    exact = len(normalized_lines) <= 8 and all(origin_options) and _combination_count(origin_options) <= 4096
    if exact:
        assignments = list(product(*origin_options))
        algorithm = "EXACT_CARTESIAN_SMALL_CART"
    else:
        assignment = tuple(options[0] for options in origin_options)
        assignments = [assignment]
        algorithm = "DETERMINISTIC_FIRST_FEASIBLE_LARGE_CART"
    candidates = [_serialize_assignment(assignment, normalized_lines, customer_charge, minimum_margin) for assignment in assignments]
    protected = [candidate for candidate in candidates if candidate["margin_protected"]]
    eligible_pool = protected or candidates
    min_guides = min(eligible_pool, key=lambda row: (row["guide_count"], row["logistics_cost"], stable_fingerprint(row["assignment"])))
    min_cost = min(eligible_pool, key=lambda row: (row["logistics_cost"], row["guide_count"], stable_fingerprint(row["assignment"])))
    max_margin = max(eligible_pool, key=lambda row: (row["margin_after_logistics"], -row["guide_count"], stable_fingerprint(row["assignment"])))
    strategies = [
        {"code": "MIN_GUIDES", "label": "Menor número de guías", **min_guides},
        {"code": "MIN_COST", "label": "Menor costo logístico", **min_cost},
        {"code": "PROTECTED_MARGIN", "label": "Mayor margen protegido", **max_margin},
    ]
    if not protected:
        blockers.append("SHIPPING_BREAKS_MINIMUM_MARGIN")
    recommended = min_cost if protected else None
    return {
        "status": "DEMO_ESTIMATED" if demo_fixture else ("ESTIMATED_LOCAL" if protected else "BLOCKED_MARGIN"),
        "quote_basis": "LOCAL_FIXTURE_DEMO" if demo_fixture else "LOCAL_PACKAGE_FIXTURE_NOT_CURRENT_RATE",
        "algorithm": algorithm,
        "algorithm_note": "Enumeración exacta para carritos pequeños; en carritos grandes se elige de forma determinista el primer origen factible por SKU.",
        "strategies": strategies,
        "recommended_strategy": ({"code": "MIN_COST", "reason": "Menor costo sujeto a stock y margen; desempate por menos guías."} if recommended else None),
        "blockers": sorted(set(blockers)),
        "planning_eligible": bool(protected),
        "commercially_eligible": False,
        "one_guide_per_origin": True, "sku_splitting": False, "external_writes": 0,
    }


def _combination_count(options):
    total = 1
    for values in options:
        total *= len(values)
    return total


def demo_case(name, now=None):
    now = now or datetime.now(timezone.utc)
    known = lambda origin, available: {"origin": origin, "available": available, "unknown": False, "observed_at": now.isoformat(), "freshness_minutes": 1440}
    unknown = lambda origin: {"origin": origin, "available": None, "unknown": True, "observed_at": now.isoformat(), "freshness_minutes": 1440}
    cases = {
        "ONE_ORIGIN": [
            {"sku": "DEMO-A", "quantity": 1, "unit_price": 180000, "unit_cost": 100000, "package_ready": True, "inventory": [known("Barú Bogotá", 4)]},
        ],
        "MULTIPLE_ORIGINS": [
            {"sku": "DEMO-A", "quantity": 1, "unit_price": 180000, "unit_cost": 100000, "package_ready": True, "inventory": [known("Barú Bogotá", 4)]},
            {"sku": "DEMO-B", "quantity": 1, "unit_price": 220000, "unit_cost": 130000, "package_ready": True, "inventory": [known("Merci Cali", 3)]},
        ],
        "INSUFFICIENT_STOCK": [
            {"sku": "DEMO-C", "quantity": 3, "unit_price": 100000, "unit_cost": 60000, "package_ready": True, "inventory": [known("Barú Bogotá", 1)]},
        ],
        "UNKNOWN_WAREHOUSE": [
            {"sku": "DEMO-D", "quantity": 1, "unit_price": 100000, "unit_cost": 60000, "package_ready": True, "inventory": [unknown("Bodega pendiente")]},
        ],
        "NOT_QUOTABLE": [
            {"sku": "DEMO-E", "quantity": 1, "unit_price": 100000, "unit_cost": 60000, "package_ready": False, "inventory": [known("Barú Bogotá", 3)]},
        ],
        "SHIPPING_BREAKS_MARGIN": [
            {"sku": "DEMO-F", "quantity": 1, "unit_price": 100000, "unit_cost": 87000, "package_ready": True, "inventory": [known("Barú Bogotá", 3)]},
        ],
        "TIE": [
            {"sku": "DEMO-G", "quantity": 1, "unit_price": 200000, "unit_cost": 90000, "package_ready": True, "inventory": [known("Bodega A", 3), known("Bodega B", 3)]},
        ],
    }
    if name not in cases:
        raise ValueError("Caso DEMO desconocido")
    demo = name != "NOT_QUOTABLE"
    minimum = 15 if name == "SHIPPING_BREAKS_MARGIN" else 10
    return simulate_multwarehouse_cart(cases[name], demo_fixture=demo, customer_shipping_charge=3000, minimum_margin_percent=minimum, now=now)
