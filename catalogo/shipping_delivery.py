"""Primera fase local del módulo Envíos y entrega.

No cotiza transportadoras, no crea guías y no modifica Shopify. La simulación
solo permite evaluar un envío estándar por ciudad/departamento y proteger el
margen mínimo del pedido después de descuentos y subsidio logístico.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .phase7 import build_average_shipping_reference


ZERO = Decimal("0")
HUNDRED = Decimal("100")
MINIMUM_MARGIN_PERCENT = Decimal("20")
FULFILLMENT_ORIGINS = {"ENVIA", "SUPPLIER"}


class ShippingDeliveryInputError(ValueError):
    pass


def _decimal(payload, key, *, required=True, minimum=ZERO, maximum=None):
    value = payload.get(key)
    if value in (None, ""):
        if required:
            raise ShippingDeliveryInputError(f"Falta {key}.")
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ShippingDeliveryInputError(f"{key} debe ser un número válido.") from error
    if number < minimum or (maximum is not None and number > maximum):
        limit = f" entre {minimum} y {maximum}" if maximum is not None else f" mayor o igual a {minimum}"
        raise ShippingDeliveryInputError(f"{key} debe ser{limit}.")
    return number


def _money(value):
    return str(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _percent(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _average_reference_payload():
    reference = build_average_shipping_reference()
    if not reference:
        return {
            "available": False,
            "amount": None,
            "currency": "COP",
            "sample_size": 0,
            "basis": "NO_VERIFIED_REALIZED_GUIDE_HISTORY",
            "label": "Sin promedio histórico verificable",
        }
    return {
        "available": True,
        "amount": _money(reference["amount"]),
        "currency": reference.get("currency") or "COP",
        "sample_size": reference.get("sample_size") or 0,
        "basis": reference.get("basis"),
        "label": "Promedio informativo de guías históricas",
    }


def shipping_delivery_workspace():
    return {
        "module": {
            "title": "Envíos y entrega",
            "phase": "FASE_1_LOCAL",
            "status": "SIMULATION_ONLY",
            "plain_status": "Simulación local · sin escrituras",
        },
        "customer_options": [
            {
                "code": "STANDARD",
                "label": "Envío estándar",
                "description": "La opción económica elegible según origen, ciudad y disponibilidad.",
                "active": True,
            },
        ],
        "destination": {
            "required_fields": ["department", "city"],
            "postal_code_required": False,
            "country": "CO",
            "note": "El código postal no se exige en esta fase; una transportadora podrá solicitarlo más adelante para la tarifa final.",
        },
        "fulfillment_origins": [
            {
                "code": "ENVIA",
                "label": "Bodega Envía",
                "description": "Inventario almacenado en Envía. En esta fase solo se ofrece servicio estándar.",
            },
            {
                "code": "SUPPLIER",
                "label": "Despacho del proveedor",
                "description": "La promesa depende de que el proveedor confirme inventario, costo y tiempo de despacho.",
            },
        ],
        "commercial_policy": {
            "minimum_margin_percent": _percent(MINIMUM_MARGIN_PERCENT),
            "wholesale_rule": "El margen se valida sobre el pedido completo después del descuento y del subsidio logístico.",
            "below_minimum_action": "Reducir descuento o subsidio, o enviar el pedido a aprobación. Nunca vender automáticamente con pérdida.",
        },
        "average_shipping_reference": _average_reference_payload(),
        "phase_2": {
            "active": False,
            "items": [
                "Envío estándar gratis condicionado por margen y reserva",
                "Envío rápido pagando la diferencia",
                "Entrega el mismo día para inventario elegible en Envía",
                "Integración con Unilogix cuando su API esté disponible",
            ],
        },
        "external_writes": 0,
        "execution_allowed_external": False,
    }


def simulate_standard_shipping(payload):
    city = str(payload.get("city") or "").strip()
    department = str(payload.get("department") or "").strip()
    if not city:
        raise ShippingDeliveryInputError("Falta city.")
    if not department:
        raise ShippingDeliveryInputError("Falta department.")

    origin = str(payload.get("fulfillment_origin") or "").strip().upper()
    if origin not in FULFILLMENT_ORIGINS:
        raise ShippingDeliveryInputError("fulfillment_origin debe ser ENVIA o SUPPLIER.")

    subtotal = _decimal(payload, "order_subtotal", minimum=Decimal("1"))
    total_cost = _decimal(payload, "product_cost_total", minimum=ZERO)
    discount_percent = _decimal(
        payload,
        "wholesale_discount_percent",
        required=False,
        minimum=ZERO,
        maximum=HUNDRED,
    ) or ZERO
    shipping_estimate = _decimal(payload, "standard_shipping_estimate", minimum=Decimal("1"))
    customer_charge = _decimal(payload, "customer_shipping_charge", required=False, minimum=ZERO) or ZERO

    discount_amount = subtotal * discount_percent / HUNDRED
    net_product_revenue = subtotal - discount_amount
    if net_product_revenue <= ZERO:
        raise ShippingDeliveryInputError("El descuento deja el pedido sin ingreso neto.")

    shipping_subsidy = max(ZERO, shipping_estimate - customer_charge)
    recovered_shipping = min(shipping_estimate, customer_charge)
    profit_after_shipping = net_product_revenue - total_cost - shipping_subsidy
    margin_percent = profit_after_shipping / net_product_revenue * HUNDRED
    protected = margin_percent >= MINIMUM_MARGIN_PERCENT

    safe_revenue_floor = (total_cost + shipping_subsidy) / (Decimal("1") - MINIMUM_MARGIN_PERCENT / HUNDRED)
    safe_discount = max(ZERO, min(HUNDRED, (Decimal("1") - safe_revenue_floor / subtotal) * HUNDRED))
    warnings = []
    if customer_charge > shipping_estimate:
        warnings.append("El cobro al cliente supera la estimación de envío; revisar antes de publicar.")
    if origin == "SUPPLIER":
        warnings.append("Confirmar inventario, costo y tiempo de despacho con el proveedor antes de prometer una fecha.")
    if not protected:
        warnings.append("El descuento o subsidio deja el margen del pedido por debajo del 20%.")

    return {
        "option": {
            "code": "STANDARD",
            "label": "Envío estándar",
            "status": "ELIGIBLE" if protected else "REVIEW_REQUIRED",
            "same_day": False,
            "free_shipping": False,
        },
        "destination": {
            "city": city,
            "department": department,
            "country": "CO",
            "postal_code_used": False,
        },
        "fulfillment": {
            "origin": origin,
            "label": "Bodega Envía" if origin == "ENVIA" else "Despacho del proveedor",
            "promise": (
                "Entrega estándar sujeta a la cotización final y disponibilidad en Envía."
                if origin == "ENVIA"
                else "Entrega estándar sujeta a confirmación del proveedor."
            ),
        },
        "commercial": {
            "order_subtotal": _money(subtotal),
            "discount_percent": _percent(discount_percent),
            "discount_amount": _money(discount_amount),
            "net_product_revenue": _money(net_product_revenue),
            "product_cost_total": _money(total_cost),
            "standard_shipping_estimate": _money(shipping_estimate),
            "customer_shipping_charge": _money(customer_charge),
            "shipping_recovered": _money(recovered_shipping),
            "company_shipping_subsidy": _money(shipping_subsidy),
            "profit_after_shipping": _money(profit_after_shipping),
            "margin_percent": _percent(margin_percent),
            "minimum_margin_percent": _percent(MINIMUM_MARGIN_PERCENT),
            "maximum_safe_discount_percent": _percent(safe_discount),
            "margin_protected": protected,
        },
        "decision": {
            "code": "STANDARD_ELIGIBLE" if protected else "MANUAL_APPROVAL_REQUIRED",
            "label": "Pedido protegido" if protected else "Requiere ajuste o aprobación",
            "recommendation": (
                "Puede continuar con envío estándar; la tarifa final se confirma al completar la dirección."
                if protected
                else "Reduzca el descuento o el subsidio de envío antes de continuar."
            ),
        },
        "quote_basis": "MANUAL_CITY_ESTIMATE_NOT_CARRIER_QUOTE",
        "warnings": warnings,
        "external_writes": 0,
    }
