from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from .models import PricingPolicy, ProviderConfig


ZERO = Decimal("0")
HUNDRED = Decimal("100")


class PricingInputError(ValueError):
    pass


def as_decimal(value):
    return Decimal(str(value or 0))


def round_up(value, increment):
    step = max(as_decimal(increment), Decimal("1"))
    return (value / step).quantize(Decimal("1"), rounding=ROUND_CEILING) * step


def normalize_provider_cost(provider, supplier_price, sku_adjustment=None):
    if provider.tax_treatment == ProviderConfig.TaxTreatment.PENDING:
        raise PricingInputError("Confirma si el precio del proveedor incluye IVA antes de calcular.")
    if provider.tax_rate is None:
        raise PricingInputError("Falta la tasa de IVA del proveedor.")

    price = as_decimal(supplier_price)
    discount = as_decimal(provider.general_discount_percent)
    fixed_adjustment = ZERO
    if sku_adjustment:
        discount += as_decimal(sku_adjustment.discount_percent)
        fixed_adjustment += as_decimal(sku_adjustment.fixed_charge)
    discounted = price * (Decimal("1") - discount / HUNDRED)
    if provider.tax_treatment == ProviderConfig.TaxTreatment.EXCLUDED:
        discounted *= Decimal("1") + as_decimal(provider.tax_rate) / HUNDRED
    provider_charge = discounted * as_decimal(provider.charge_percent) / HUNDRED
    return discounted + provider_charge + as_decimal(provider.fixed_charge) + fixed_adjustment


def policy_specificity(policy):
    fields = [policy.channel, policy.provider_id, policy.collection, policy.brand, policy.category, policy.product_type, policy.sku]
    return sum(bool(value) for value in fields) + len(policy.combination or {})


def select_policy(policies, context):
    precedence_rank = {
        PricingPolicy.Precedence.GENERAL: 1,
        PricingPolicy.Precedence.SPECIFIC: 2,
        PricingPolicy.Precedence.EXCEPTION: 3,
    }

    def matches(policy):
        checks = {
            "channel": policy.channel,
            "provider_id": policy.provider_id,
            "collection": policy.collection,
            "brand": policy.brand,
            "category": policy.category,
            "product_type": policy.product_type,
            "sku": policy.sku,
        }
        if any(expected and context.get(key) != expected for key, expected in checks.items()):
            return False
        return all(context.get(key) == value for key, value in (policy.combination or {}).items())

    candidates = [policy for policy in policies if policy.active and matches(policy)]
    if not candidates:
        return None
    return max(candidates, key=lambda policy: (precedence_rank[policy.precedence], policy_specificity(policy), policy.priority, -policy.pk))


@dataclass
class PricingResult:
    normalized_cost: Decimal
    proposed_price: Decimal
    commission_amount: Decimal
    shipping_subsidy: Decimal
    achieved_margin_percent: Decimal
    formula: dict
    reason: str
    warnings: list


def calculate_price(*, provider, supplier_price, policy, quoted_shipping=0, customer_shipping_charge=0, sku_adjustment=None):
    normalized_cost = normalize_provider_cost(provider, supplier_price, sku_adjustment)
    quoted_shipping = as_decimal(quoted_shipping)
    customer_shipping_charge = as_decimal(customer_shipping_charge)
    subsidy = max(ZERO, quoted_shipping - customer_shipping_charge)
    warnings = []
    if subsidy > as_decimal(policy.max_shipping_subsidy):
        warnings.append("El subsidio supera el tope de la política.")

    reserve_cap = as_decimal(policy.logistics_reserve or provider.logistics_reserve)
    reserve_applied = reserve_cap if policy.reserve_behavior == PricingPolicy.ReserveBehavior.INCLUDED_IN_PRICE else ZERO
    if policy.reserve_behavior == PricingPolicy.ReserveBehavior.CAP and reserve_cap > ZERO and subsidy > reserve_cap:
        warnings.append("El subsidio supera la reserva logística máxima de protección.")
    fixed = as_decimal(policy.channel_fixed_charge) + reserve_applied + subsidy
    margin_rate = as_decimal(policy.target_margin_percent) / HUNDRED
    commission_rate = as_decimal(policy.channel_commission_percent) / HUNDRED
    denominator = Decimal("1") - margin_rate - commission_rate
    if denominator <= ZERO:
        raise PricingInputError("Margen objetivo y comisión dejan una base de venta inválida.")

    proposed = round_up((normalized_cost + fixed) / denominator, policy.rounding_increment or provider.rounding_increment)
    commission = proposed * commission_rate
    realized_margin = (proposed - normalized_cost - fixed - commission) / proposed * HUNDRED
    if realized_margin < as_decimal(policy.target_margin_percent):
        warnings.append("El redondeo o los cargos dejan el margen por debajo del objetivo.")
    reason = policy.explanation or f"Aplica {policy.get_precedence_display().lower()}: {policy.name}."
    return PricingResult(
        normalized_cost=normalized_cost,
        proposed_price=proposed,
        commission_amount=commission,
        shipping_subsidy=subsidy,
        achieved_margin_percent=realized_margin,
        formula={
            "plain_language": "Precio = (costo normalizado + cargos fijos + reserva aplicada + subsidio) / (1 - margen real - comisión), redondeado hacia arriba. Cuando la reserva es un tope, no se suma al precio.",
            "supplier_gross_cost": str(as_decimal(supplier_price)),
            "tax_treatment": provider.tax_treatment,
            "tax_rate": str(provider.tax_rate),
            "tax_adjustment": "0" if provider.tax_treatment == ProviderConfig.TaxTreatment.INCLUDED else "IVA agregado una vez",
            "double_tax_guard": provider.tax_treatment == ProviderConfig.TaxTreatment.INCLUDED,
            "cost": str(normalized_cost),
            "fixed_charges": str(as_decimal(policy.channel_fixed_charge)),
            "logistics_reserve_cap": str(reserve_cap),
            "logistics_reserve_applied": str(reserve_applied),
            "reserve_behavior": policy.reserve_behavior,
            "shipping_subsidy": str(subsidy),
            "target_margin_percent": str(policy.target_margin_percent),
            "commission_percent": str(policy.channel_commission_percent),
            "rounding_increment": policy.rounding_increment or provider.rounding_increment,
        },
        reason=reason,
        warnings=warnings,
    )


def margin_at_price(*, product_price, normalized_cost, policy, quoted_shipping, customer_shipping_charge, logistics_reserve=None):
    price = as_decimal(product_price)
    if price <= ZERO:
        raise PricingInputError("Falta un precio de venta positivo para comprobar la modalidad de envío.")
    subsidy = max(ZERO, as_decimal(quoted_shipping) - as_decimal(customer_shipping_charge))
    reserve_cap = as_decimal(policy.logistics_reserve if logistics_reserve is None else logistics_reserve)
    reserve = reserve_cap if policy.reserve_behavior == PricingPolicy.ReserveBehavior.INCLUDED_IN_PRICE else ZERO
    commission = price * as_decimal(policy.channel_commission_percent) / HUNDRED
    profit = price - as_decimal(normalized_cost) - as_decimal(policy.channel_fixed_charge) - reserve - subsidy - commission
    return profit / price * HUNDRED


def commercial_sensitivity(*, provider, supplier_price, policy, quoted_shipping, margins=(20, 25, 30, 35, 40), customer_charges=None):
    """Editable local scenarios. They are evidence, never policy approval."""
    quoted = as_decimal(quoted_shipping)
    charges = customer_charges or (quoted, Decimal("3000"), Decimal("2000"), ZERO)
    normalized = normalize_provider_cost(provider, supplier_price)
    commission_rate = as_decimal(policy.channel_commission_percent) / HUNDRED
    reserve_cap = as_decimal(policy.logistics_reserve or provider.logistics_reserve)
    reserve_applied = reserve_cap if policy.reserve_behavior == PricingPolicy.ReserveBehavior.INCLUDED_IN_PRICE else ZERO
    scenarios = []
    for margin in margins:
        margin_rate = as_decimal(margin) / HUNDRED
        denominator = Decimal("1") - margin_rate - commission_rate
        if denominator <= ZERO:
            continue
        for customer_charge in charges:
            customer_charge = as_decimal(customer_charge)
            subsidy = max(ZERO, quoted - customer_charge)
            fixed = as_decimal(policy.channel_fixed_charge) + reserve_applied + subsidy
            required = round_up((normalized + fixed) / denominator, policy.rounding_increment or provider.rounding_increment)
            scenarios.append({
                "target_margin_percent": str(as_decimal(margin)),
                "customer_shipping_charge": str(customer_charge),
                "shipping_subsidy": str(subsidy),
                "break_even_price": str(round_up((normalized + fixed) / (Decimal("1") - commission_rate), policy.rounding_increment or provider.rounding_increment)),
                "required_price": str(required),
                "reserve_cap": str(reserve_cap),
                "reserve_applied": str(reserve_applied),
                "eligible_by_caps": subsidy <= as_decimal(policy.max_shipping_subsidy) and (reserve_cap <= ZERO or subsidy <= reserve_cap),
            })
    return scenarios


def shipping_options(*, provider, supplier_price, policy, quoted_shipping=None, sku_adjustment=None,
                     current_product_price=None, logistics_inputs_complete=True):
    if quoted_shipping is None or not logistics_inputs_complete:
        reason = "Faltan peso, dimensiones o una cotización verificable; no se habilita ninguna modalidad."
        return [{
            "customer_charge": charge,
            "subsidy": None,
            "supported": False,
            "required_product_price": None,
            "margin_at_current_price": None,
            "warnings": [reason],
            "status": "BLOCKED_MISSING_LOGISTICS_INPUT",
        } for charge in ["REAL_RATE", Decimal("3000"), Decimal("2000"), ZERO]]

    options = []
    for customer_charge in [as_decimal(quoted_shipping), Decimal("3000"), Decimal("2000"), ZERO]:
        result = calculate_price(
            provider=provider,
            supplier_price=supplier_price,
            policy=policy,
            quoted_shipping=quoted_shipping,
            customer_shipping_charge=customer_charge,
            sku_adjustment=sku_adjustment,
        )
        minimum_margin = as_decimal(policy.minimum_margin_percent if policy.minimum_margin_percent is not None else policy.target_margin_percent)
        margin_at_current = None
        warnings = list(result.warnings)
        if current_product_price is not None:
            margin_at_current = margin_at_price(
                product_price=current_product_price,
                normalized_cost=result.normalized_cost,
                policy=policy,
                quoted_shipping=quoted_shipping,
                customer_shipping_charge=customer_charge,
                logistics_reserve=policy.logistics_reserve or provider.logistics_reserve,
            )
            if margin_at_current < minimum_margin:
                warnings.append(f"El precio actual deja {margin_at_current:.2f}% de margen, por debajo del mínimo {minimum_margin:.2f}%.")
        options.append({
            "customer_charge": customer_charge,
            "subsidy": result.shipping_subsidy,
            "supported": not warnings,
            "required_product_price": result.proposed_price,
            "margin_at_current_price": margin_at_current,
            "warnings": warnings,
            "status": "ELIGIBLE" if not warnings else "BLOCKED_MARGIN_OR_CAP",
        })
    return options
