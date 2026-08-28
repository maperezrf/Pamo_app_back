"""Primera fase local del módulo Envíos y entrega.

No cotiza transportadoras, no crea guías y no modifica Shopify. La simulación
solo permite evaluar un envío estándar por ciudad/departamento y proteger el
margen mínimo del pedido después de descuentos y subsidio logístico.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from django.utils import timezone

from .commercial_costs import DRIVE_COST_RULES
from .envia_readiness import readiness_sets
from .models import CatalogHistoryEvent, IntegrationReadStatus, LogisticsQuoteSnapshot, ProductVariant
from .phase7 import average_shipping_for_variant, build_average_shipping_reference


ZERO = Decimal("0")
HUNDRED = Decimal("100")
MINIMUM_MARGIN_PERCENT = Decimal("20")
FULFILLMENT_ORIGINS = {"ENVIA", "SUPPLIER"}
DANE_CODE = re.compile(r"^\d{8}$")
CONNECTION_FRESHNESS_HOURS = 6


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


def _connector_status(system, *, primary_capability, label, purpose, strategy):
    now = timezone.now()
    statuses = list(IntegrationReadStatus.objects.filter(system=system))
    primary = next((row for row in statuses if row.capability == primary_capability), None)
    last_attempt = max((row.observed_at for row in statuses), default=None)
    last_success = max((row.last_success_at for row in statuses if row.last_success_at), default=None)
    fresh = bool(
        last_success
        and (now - last_success).total_seconds() <= CONNECTION_FRESHNESS_HOURS * 3600
    )
    available = bool(primary and primary.status == IntegrationReadStatus.Status.AVAILABLE)
    blockers = [
        {"capability": row.capability, "message": row.message}
        for row in statuses
        if row.status in {
            IntegrationReadStatus.Status.BLOCKED,
            IntegrationReadStatus.Status.MISSING,
            IntegrationReadStatus.Status.NOT_AUTHORIZED,
        }
    ]
    if available and fresh:
        state, state_label = "CONNECTED", "Conectado"
    elif available:
        state, state_label = "STALE", "Conexión por verificar"
    elif statuses:
        state, state_label = "DEGRADED", "Con información parcial"
    else:
        state, state_label = "DISCONNECTED", "Sin conexión verificada"
    return {
        "system": system,
        "label": label,
        "purpose": purpose,
        "strategy": strategy,
        "status": state,
        "status_label": state_label,
        "fresh": fresh,
        "last_attempt_at": last_attempt,
        "last_success_at": last_success,
        "record_count": primary.record_count if primary else None,
        "message": primary.message if primary else "Todavía no hay una comprobación registrada.",
        "blockers": blockers,
        "external_writes": sum(row.external_writes for row in statuses),
    }


def _connection_history():
    events = list(
        CatalogHistoryEvent.objects.filter(entity_type="ShippingConnector")
        .order_by("-created_at")[:30]
    )
    if events:
        return [
            {
                "system": event.entity_id,
                "action": event.action,
                "status": (event.after or {}).get("status"),
                "message": (event.after or {}).get("message"),
                "records": (event.after or {}).get("record_count"),
                "created_at": event.created_at,
                "external_writes": (event.after or {}).get("external_writes", 0),
            }
            for event in events
        ]
    return [
        {
            "system": row.system,
            "action": "ÚLTIMA_LECTURA_REGISTRADA",
            "status": row.status,
            "message": row.message,
            "records": row.record_count,
            "created_at": row.observed_at,
            "external_writes": row.external_writes,
        }
        for row in IntegrationReadStatus.objects.filter(
            system__in=["SHOPIFY", "ENVIA"],
        ).order_by("-observed_at")[:30]
    ]


def shipping_delivery_workspace():
    _, package_complete, quoted = readiness_sets()
    connectors = [
        _connector_status(
            "SHOPIFY",
            primary_capability="marketplace_catalog_snapshot",
            label="Shopify",
            purpose="Inventario, precio y ubicación de despacho",
            strategy="Webhook en la futura integración + lectura completa de respaldo",
        ),
        _connector_status(
            "ENVIA",
            primary_capability="shipping_api_connection",
            label="Envía",
            purpose="Cotización estándar por origen, destino y paquete",
            strategy="Cotización al consultar el producto o el carrito + caché temporal",
        ),
    ]
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
        "engine_configuration": [
            {"key": "PLAN_A", "label": "Plan A", "value": "Cotización actual de Envía", "detail": "Se usa únicamente con origen, destino DANE, peso y medidas verificables."},
            {"key": "PLAN_B", "label": "Plan B", "value": "Perfil histórico aproximado", "detail": "Mantiene una referencia si falta un dato o Envía no responde; nunca se publica como tarifa definitiva."},
            {"key": "ORIGIN", "label": "Origen", "value": "Ubicación Shopify con inventario", "detail": "El motor asigna la bodega real antes de cotizar."},
            {"key": "DESTINATION", "label": "Destino", "value": "Ciudad, departamento y dirección", "detail": "La ciudad se resuelve al código DANE requerido por Envía; la dirección no se conserva en el preview."},
            {"key": "RESERVE", "label": "Reserva logística", "value": "4% · máximo $40.000 por unidad", "detail": "Solo subsidia hasta donde permita la reserva y el margen."},
            {"key": "MARGIN", "label": "Margen mínimo", "value": "20%", "detail": "Se recalcula después de descuentos mayoristas y subsidio."},
            {"key": "SERVICE", "label": "Servicio activo", "value": "Envío estándar", "detail": "Envío rápido, gratis automático y mismo día quedan para una fase posterior."},
        ],
        "readiness": {
            "variants_with_positive_stock": ProductVariant.objects.filter(
                inventory_levels__available__gt=0,
                inventory_levels__location_active=True,
            ).distinct().count(),
            "package_complete": len(package_complete),
            "current_route_quotes": len(quoted),
            "note": "La conexión puede estar disponible aunque algunos productos sigan bloqueados por peso o medidas.",
        },
        "connections": connectors,
        "connection_history": _connection_history(),
        "monitoring": {
            "freshness_hours": CONNECTION_FRESHNESS_HOURS,
            "absolute_guarantee": False,
            "plain_note": "Ninguna API puede garantizarse al 100%. El tablero verifica actualidad, conserva el último dato correcto y activa el Plan B si la lectura falla.",
        },
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


def _variant_by_sku(sku):
    matches = list(
        ProductVariant.objects.select_related("product")
        .prefetch_related("inventory_levels", "logistics_quotes", "channel_snapshots")
        .filter(sku__iexact=sku)
        .exclude(product__status="STALE_LOCAL_SNAPSHOT")[:2]
    )
    if not matches:
        raise ShippingDeliveryInputError(f"El SKU {sku} no existe en el catálogo Shopify local.")
    if len(matches) > 1:
        raise ShippingDeliveryInputError(f"El SKU {sku} está duplicado y requiere revisión.")
    return matches[0]


def _allocate_origins(variant, quantity):
    levels = [
        level for level in variant.inventory_levels.all()
        if level.location_active and level.available is not None and level.available > 0
    ]
    levels.sort(key=lambda level: (
        not level.fulfills_online_orders,
        "bodega envia" not in level.location_name.casefold(),
        -level.available,
        level.location_name.casefold(),
    ))
    remaining = Decimal(quantity)
    allocations = []
    for level in levels:
        if remaining <= 0:
            break
        allocated = min(remaining, level.available)
        allocations.append({
            "location_id": level.location_external_id,
            "location_name": level.location_name,
            "quantity": _money(allocated),
            "address": level.origin_address,
            "address_verified": level.address_verified,
            "fulfills_online_orders": level.fulfills_online_orders,
            "observed_at": level.observed_at,
            "live_quote_origin_ready": bool(
                level.origin_address.get("address1")
                and level.origin_address.get("city")
                and level.origin_address.get("countryCode")
            ),
        })
        remaining -= allocated
    return allocations, remaining


def _quote_matches_destination(quote, city, department, dane_code):
    destination = quote.destination or {}
    quoted_city = str(destination.get("city") or "").strip()
    quoted_state = str(destination.get("state") or destination.get("department") or "").strip()
    if dane_code and quoted_city == dane_code:
        return True
    return (
        quoted_city.casefold() == city.casefold()
        and (not quoted_state or quoted_state.casefold() == department.casefold())
    )


def _shipping_reference(variant, city, department, dane_code, average_reference):
    quotes = [
        quote for quote in variant.logistics_quotes.all()
        if quote.provider == "ENVIA"
        and quote.basis == LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE
        and quote.status == "AVAILABLE"
        and quote.amount is not None
        and _quote_matches_destination(quote, city, department, dane_code)
    ]
    if quotes:
        newest = max(quote.observed_at for quote in quotes)
        current = [quote for quote in quotes if quote.observed_at == newest]
        selected = min(current, key=lambda quote: quote.amount)
        return {
            "amount": selected.amount,
            "currency": selected.currency,
            "basis": "ENVIA_CURRENT_ROUTE_QUOTE",
            "classification": "CURRENT_NON_BINDING",
            "carrier": selected.carrier,
            "delivery_estimate": selected.delivery_estimate or None,
            "observed_at": selected.observed_at,
            "selection_policy": "CHEAPEST_STANDARD_PREVIEW",
            "requires_final_quote": True,
        }
    average = average_shipping_for_variant(variant, average_reference)
    if not average or average.get("amount") is None:
        return None
    return {
        "amount": Decimal(str(average["amount"])),
        "currency": average.get("currency") or "COP",
        "basis": average.get("basis") or "HISTORICAL_GUIDE_REFERENCE",
        "classification": "APPROXIMATE_HISTORICAL",
        "carrier": None,
        "delivery_estimate": None,
        "observed_at": None,
        "tariff_band": average.get("tariff_band"),
        "requires_final_quote": True,
    }


def estimate_catalog_shipping(payload):
    """Contrato Beta para la web: calcula el pedido sin crear guías ni escribir Shopify."""
    destination = payload.get("destination") or {}
    city = str(destination.get("city") or "").strip()
    department = str(destination.get("department") or "").strip()
    address = str(destination.get("address") or "").strip()
    dane_code = str(destination.get("dane_code") or "").strip()
    if not city or not department:
        raise ShippingDeliveryInputError("El destino requiere city y department.")
    if dane_code and not DANE_CODE.fullmatch(dane_code):
        raise ShippingDeliveryInputError("dane_code debe tener 8 dígitos para Colombia.")
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise ShippingDeliveryInputError("El pedido requiere por lo menos un SKU.")
    discount_percent = _decimal(
        payload, "wholesale_discount_percent", required=False,
        minimum=ZERO, maximum=HUNDRED,
    ) or ZERO

    average_reference = build_average_shipping_reference()
    lines = []
    subtotal = total_cost = total_reserve = shipping_total = ZERO
    missing_cost = False
    approximate = False
    total_units = ZERO
    for item in items:
        sku = str((item or {}).get("sku") or "").strip()
        if not sku:
            raise ShippingDeliveryInputError("Cada producto requiere sku.")
        try:
            quantity = Decimal(str((item or {}).get("quantity") or 1))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ShippingDeliveryInputError(f"La cantidad de {sku} no es válida.") from error
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ShippingDeliveryInputError(f"La cantidad de {sku} debe ser un entero positivo.")
        variant = _variant_by_sku(sku)
        if variant.price is None or variant.price <= 0:
            raise ShippingDeliveryInputError(f"El SKU {sku} no tiene precio Shopify válido.")
        allocations, shortage = _allocate_origins(variant, quantity)
        if shortage > 0:
            raise ShippingDeliveryInputError(
                f"El SKU {sku} no tiene inventario suficiente por ubicación; faltan {_money(shortage)} unidades."
            )
        reference = _shipping_reference(
            variant, city, department, dane_code, average_reference,
        )
        if not reference:
            raise ShippingDeliveryInputError(f"El SKU {sku} no tiene una referencia de envío verificable.")
        approximate = approximate or reference["classification"] != "CURRENT_NON_BINDING"
        line_shipping = reference["amount"] * quantity
        line_reserve = min(variant.price * Decimal("0.04"), Decimal("40000")) * quantity
        line_cost = variant.provider_cost * quantity if variant.provider_cost else None
        subtotal += variant.price * quantity
        shipping_total += line_shipping
        total_reserve += line_reserve
        total_units += quantity
        if line_cost is None:
            missing_cost = True
        else:
            total_cost += line_cost
        lines.append({
            "sku": variant.sku,
            "title": variant.product.title,
            "quantity": _money(quantity),
            "unit_price": _money(variant.price),
            "unit_cost": _money(variant.provider_cost) if variant.provider_cost else None,
            "origins": allocations,
            "shipping": {**reference, "amount": _money(line_shipping)},
            "logistics_reserve": _money(line_reserve),
        })

    net_revenue = subtotal * (HUNDRED - discount_percent) / HUNDRED
    rule = DRIVE_COST_RULES["SHOPIFY"]
    commission = (
        net_revenue * rule["commission_percent"] / HUNDRED
        + rule["commission_fixed"] * total_units
    ) * (Decimal("1") + rule["commission_tax_percent"] / HUNDRED)
    administration = net_revenue * rule["administrative_percent"] / HUNDRED
    internal_fixed = (rule["operating_fixed"] + rule["additional_fixed"]) * total_units
    margin_floor = net_revenue * rule["target_net_margin_percent"] / HUNDRED
    profit_before_subsidy = (
        net_revenue - total_cost - commission - administration - internal_fixed
        if not missing_cost else ZERO
    )
    margin_capacity = max(ZERO, profit_before_subsidy - margin_floor)
    protected_subsidy = (
        min(shipping_total, total_reserve, margin_capacity)
        if not missing_cost else ZERO
    )
    customer_charge = max(ZERO, shipping_total - protected_subsidy)
    free_shipping = customer_charge == ZERO and not approximate and not missing_cost
    warnings = []
    if approximate:
        warnings.append("La tarifa es histórica y aproximada; debe reemplazarse por la cotización Envía de la ruta antes de prometer el cobro.")
    if not dane_code:
        warnings.append("Falta resolver la ciudad al código DANE de 8 dígitos para consultar Envía en tiempo real.")
    if not address:
        warnings.append("La dirección se solicitará en checkout para completar la cotización final; no se guarda en este preview.")
    if any(not origin["live_quote_origin_ready"] for line in lines for origin in line["origins"]):
        warnings.append("Una o más bodegas no tienen dirección de origen completa para cotizar en tiempo real.")
    if missing_cost:
        warnings.append("Hay productos sin costo; el sistema no aplica subsidio ni promete envío gratis.")

    return {
        "status": "LIVE_QUOTE_PREVIEW" if not approximate else "APPROXIMATE_PREVIEW",
        "destination": {
            "city": city, "department": department, "dane_code": dane_code or None,
            "country": "CO", "address_received": bool(address),
        },
        "items": lines,
        "order": {
            "subtotal": _money(subtotal),
            "wholesale_discount_percent": _percent(discount_percent),
            "net_revenue": _money(net_revenue),
            "estimated_shipping": _money(shipping_total),
            "logistics_reserve_available": _money(total_reserve),
            "margin_safe_subsidy": _money(protected_subsidy),
            "customer_shipping_charge": _money(customer_charge),
            "free_shipping": free_shipping,
            "minimum_margin_percent": _percent(rule["target_net_margin_percent"]),
        },
        "website_contract": {
            "input": "city + department + address + cart SKUs; DANE resolved before live quote",
            "output": "standard shipping amount, origin, quote basis and delivery estimate",
            "publishable_now": not approximate and not missing_cost and bool(dane_code) and bool(address),
        },
        "warnings": warnings,
        "guide_created": False,
        "shopify_written": False,
        "external_writes": 0,
    }
