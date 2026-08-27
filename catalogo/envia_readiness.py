from collections import defaultdict

from django.utils import timezone

from .models import LogisticsQuoteSnapshot, PhysicalEvidenceCandidate, PhysicalEvidenceDecision
from .phase7 import average_shipping_for_variant, build_average_shipping_reference


REQUIRED_PACKAGE_FIELDS = {
    PhysicalEvidenceCandidate.Field.WEIGHT,
    PhysicalEvidenceCandidate.Field.LENGTH,
    PhysicalEvidenceCandidate.Field.WIDTH,
    PhysicalEvidenceCandidate.Field.HEIGHT,
}


def approved_package_fields_by_variant(candidates=None):
    """Return only current, explicitly approved, confirmed PACKAGE evidence."""
    now = timezone.now()
    if candidates is None:
        candidates = PhysicalEvidenceCandidate.objects.filter(
            variant__isnull=False,
            scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
            classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
            conflict=False,
        ).prefetch_related("decisions")

    fields = defaultdict(set)
    for candidate in candidates:
        if (
            not candidate.variant_id
            or candidate.scope != PhysicalEvidenceCandidate.Scope.PACKAGE
            or candidate.classification != PhysicalEvidenceCandidate.Classification.CONFIRMED
            or candidate.conflict
            or (candidate.stale_after and candidate.stale_after < now)
        ):
            continue
        latest = next(iter(candidate.decisions.all()), None)
        if not latest or latest.action != PhysicalEvidenceDecision.Action.APPROVE_LOCAL:
            continue
        if latest.expires_at and latest.expires_at < now:
            continue
        fields[candidate.variant_id].add(candidate.field)
    return fields


def current_quotes_by_variant(quotes=None):
    if quotes is None:
        quotes = LogisticsQuoteSnapshot.objects.filter(
            variant__isnull=False,
            provider="ENVIA",
            basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
            status="AVAILABLE",
            amount__isnull=False,
        ).order_by("variant_id", "-observed_at")
    latest = {}
    for quote in quotes:
        if quote.variant_id and quote.variant_id not in latest:
            latest[quote.variant_id] = quote
    return latest


def current_quote_options_by_variant(quotes=None):
    if quotes is None:
        quotes = LogisticsQuoteSnapshot.objects.filter(
            variant__isnull=False,
            provider="ENVIA",
            basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
            status="AVAILABLE",
            amount__isnull=False,
        ).order_by("variant_id", "-observed_at", "amount")
    grouped = defaultdict(list)
    latest_observed = {}
    for quote in quotes:
        if not quote.variant_id:
            continue
        if quote.variant_id not in latest_observed:
            latest_observed[quote.variant_id] = quote.observed_at
        if quote.observed_at == latest_observed[quote.variant_id]:
            grouped[quote.variant_id].append(quote)
    return grouped


def readiness_sets():
    fields = approved_package_fields_by_variant()
    complete = {variant_id for variant_id, present in fields.items() if REQUIRED_PACKAGE_FIELDS <= present}
    quoted = set(current_quote_options_by_variant()) & complete
    return fields, complete, quoted


def serialize_variant_envia_readiness(variant):
    candidates = getattr(variant, "envia_physical_candidates", None)
    quotes = getattr(variant, "envia_current_quotes", None)
    fields = approved_package_fields_by_variant(candidates).get(variant.id, set())
    quote_options = current_quote_options_by_variant(quotes).get(variant.id, [])
    quote = quote_options[0] if len(quote_options) == 1 else None
    missing = sorted(REQUIRED_PACKAGE_FIELDS - fields)

    if missing:
        status = "MISSING_PACKAGE_DATA"
    elif quote_options:
        status = "CURRENT_QUOTE_AVAILABLE" if len(quote_options) == 1 else "CURRENT_QUOTE_OPTIONS_AVAILABLE"
    elif quote:
        status = "CURRENT_QUOTE_AVAILABLE"
    else:
        status = "READY_TO_QUOTE"

    return {
        "status": status,
        "missing_fields": missing,
        "package_complete": not missing,
        "weight_confirmed": PhysicalEvidenceCandidate.Field.WEIGHT in fields,
        "dimensions_confirmed": {
            PhysicalEvidenceCandidate.Field.LENGTH,
            PhysicalEvidenceCandidate.Field.WIDTH,
            PhysicalEvidenceCandidate.Field.HEIGHT,
        } <= fields,
        "current_quote_amount": quote.amount if quote else None,
        "current_quote_currency": quote.currency if quote else None,
        "current_quote_carrier": quote.carrier if quote else None,
        "current_quote_observed_at": quote.observed_at if quote else None,
        "current_quote_options_count": len(quote_options),
        "current_quote_min_amount": min((item.amount for item in quote_options), default=None),
        "current_quote_max_amount": max((item.amount for item in quote_options), default=None),
        "current_quote_selection_required": len(quote_options) > 1,
        "current_quote_options": [
            {"carrier": item.carrier, "amount": item.amount, "currency": item.currency}
            for item in quote_options
        ],
        "evidence_policy": "PACKAGE_CONFIRMED_APPROVED_ONLY",
    }


def serialize_variant_shipping_intelligence(variant, external_snapshots=None, average_reference=None):
    """Unifica cotización transportadora y costos de marketplace sin mezclarlos.

    ``reference`` es el mejor dato operativo disponible para el vendedor, pero
    siempre conserva su base. Nunca convierte ausencia o error en costo cero.
    """
    envia = serialize_variant_envia_readiness(variant)
    external_snapshots = external_snapshots or {}
    channels = {}
    snapshots = list(getattr(variant, "channel_snapshots", []).all()) if hasattr(getattr(variant, "channel_snapshots", None), "all") else []
    for snapshot in snapshots:
        external_id = str((snapshot.payload or {}).get("external_snapshot_id") or "")
        external = external_snapshots.get(external_id)
        shipping = ((external.payload or {}).get("shipping_costs") or {}) if external else {}
        if not shipping:
            madecentro = ((external.payload or {}).get("shipping") or {}) if external and snapshot.channel == "MADECENTRO" else {}
            if madecentro:
                channels[snapshot.channel] = {
                    "status": "REFERENCE_TARIFFS_AVAILABLE",
                    "seller_estimate": None,
                    "buyer_charge": madecentro.get("bogota_cundinamarca"),
                    "buyer_list_cost": None,
                    "buyer_currency": external.currency or "COP",
                    "buyer_destination": {"label": "Bogotá / Cundinamarca"},
                    "destination_rates": {
                        "bogota_cundinamarca": madecentro.get("bogota_cundinamarca"),
                        "rest_of_colombia": madecentro.get("rest_of_colombia"),
                        "other_destinations": madecentro.get("other_destinations"),
                    },
                    "basis": {"buyer": "MADECENTRO_COMMERCIAL_WORKBOOK_REFERENCE"},
                    "observed_at": external.observed_at,
                    "errors": [],
                }
            else:
                channels[snapshot.channel] = {
                    "status": "NOT_AVAILABLE_IN_PRODUCT_SNAPSHOT",
                    "seller_estimate": None,
                    "buyer_charge": None,
                    "basis": {},
                    "observed_at": external.observed_at if external else snapshot.observed_at,
                    "errors": [],
                }
            continue
        channels[snapshot.channel] = {
            "status": shipping.get("status") or "UNKNOWN",
            "seller_estimate": shipping.get("seller_estimate"),
            "seller_currency": shipping.get("seller_currency") or external.currency or "COP",
            "buyer_charge": shipping.get("buyer_charge"),
            "buyer_list_cost": shipping.get("buyer_list_cost"),
            "buyer_currency": shipping.get("buyer_currency") or external.currency or "COP",
            "buyer_service": shipping.get("buyer_service"),
            "buyer_destination": shipping.get("buyer_destination") or {},
            "free_shipping": shipping.get("free_shipping"),
            "billable_weight_grams": shipping.get("billable_weight_grams"),
            "current_seller_estimate": shipping.get("current_seller_estimate"),
            "seller_estimate_strategy": shipping.get("seller_estimate_strategy"),
            "current_logistic_type": shipping.get("current_logistic_type"),
            "shipping_tags": shipping.get("shipping_tags") or [],
            "modalities": shipping.get("modalities") or {},
            "basis": shipping.get("basis") or {},
            "observed_at": shipping.get("observed_at") or external.observed_at,
            "errors": shipping.get("errors") or [],
        }

    average_reference = average_shipping_for_variant(
        variant,
        average_reference or build_average_shipping_reference(),
    )
    reference = None
    if envia.get("current_quote_amount") is not None:
        reference = {
            "amount": envia["current_quote_amount"],
            "currency": envia.get("current_quote_currency") or "COP",
            "source": "ENVIA",
            "basis": "CURRENT_NON_BINDING_CARRIER_QUOTE",
            "label": "Cotización actual Envía",
            "observed_at": envia.get("current_quote_observed_at"),
        }
    else:
        meli = channels.get("MERCADO_LIBRE") or {}
        if meli.get("seller_estimate") is not None:
            reference = {
                "amount": meli["seller_estimate"],
                "currency": meli.get("seller_currency") or "COP",
                "source": "MERCADO_LIBRE",
                "basis": "CURRENT_LISTING_SELLER_ESTIMATE",
                "label": "Costo estimado vendedor Mercado Libre",
                "observed_at": meli.get("observed_at"),
            }

    if reference:
        status = "REFERENCE_AVAILABLE"
    elif envia.get("status") == "MISSING_PACKAGE_DATA":
        status = "MISSING_PACKAGE_DATA"
    else:
        status = "PENDING_ROUTE_QUOTE"
    return {
        "status": status,
        "reference": reference,
        "average_shipping": average_reference,
        "carrier_quote": {
            "provider": "ENVIA",
            "amount": envia.get("current_quote_amount"),
            "currency": envia.get("current_quote_currency"),
            "carrier": envia.get("current_quote_carrier"),
            "observed_at": envia.get("current_quote_observed_at"),
            "options_count": envia.get("current_quote_options_count") or 0,
            "min_amount": envia.get("current_quote_min_amount"),
            "max_amount": envia.get("current_quote_max_amount"),
            "selection_required": envia.get("current_quote_selection_required") or False,
            "basis": "CURRENT_NON_BINDING_CARRIER_QUOTE",
        },
        "channels": channels,
        "missing_package_fields": envia.get("missing_fields") or [],
        "recommended_metric": {
            "name": "REALIZED_GUIDE_FREQUENCY_WEIGHTED_TRIMMED_MEAN",
            "available": average_reference is not None,
            "reason": "Referencia informativa ponderada por las guías históricas; no reemplaza la cotización por destino ni entra al costo del producto.",
        },
        "policy": "SEPARATE_CARRIER_BUYER_SELLER_AND_REALIZED_COSTS",
    }
