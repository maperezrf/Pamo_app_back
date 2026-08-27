"""Capa local de preparación multicanal y logística estimada.

No cotiza, no publica y no crea guías. Los perfiles se reconstruyen desde
snapshots históricos sanitizados; un segmento incompleto nunca es asignable.
"""

from collections import defaultdict
from decimal import Decimal
from statistics import median

from django.db.models import Prefetch

from .models import (
    Channel,
    ChannelSnapshot,
    ExternalChannelProductSnapshot,
    LogisticsQuoteSnapshot,
    PhysicalEvidenceCandidate,
    ProductVariant,
)


CHANNEL_MATRIX = [
    {
        "code": Channel.SHOPIFY,
        "label": "Shopify",
        "aliases": [],
        "implementation": "IMPLEMENTED_LOCAL",
        "connection": "LOCAL_SNAPSHOT",
        "adapter": "snapshot local de solo lectura",
    },
    {
        "code": Channel.MERCADO_LIBRE,
        "label": "Mercado Libre",
        "aliases": [],
        "implementation": "PREPARED",
        "connection": "NOT_CONNECTED",
        "adapter": "contrato pendiente",
    },
    {
        "code": Channel.FALABELLA,
        "label": "Falabella",
        "aliases": [],
        "implementation": "PREPARED",
        "connection": "NOT_CONNECTED",
        "adapter": "contrato pendiente",
    },
    {
        "code": Channel.MADECENTRO,
        "label": "Madecentro",
        "aliases": [],
        "implementation": "PREPARED",
        "connection": "NOT_CONNECTED",
        "adapter": "contrato pendiente",
    },
    {
        "code": Channel.RAPPI,
        "label": "Rappi",
        "aliases": [],
        "implementation": "PREPARED",
        "connection": "NOT_CONNECTED",
        "adapter": "contrato pendiente",
    },
    {
        "code": Channel.SODIMAC,
        "label": "Sodimac / Homecenter",
        "aliases": ["Homecenter"],
        "implementation": "PREPARED",
        "connection": "NOT_CONNECTED",
        "adapter": "contrato pendiente",
    },
]


def _decimal(value):
    return None if value in (None, "") else Decimal(str(value))


def _percentile(values, percentile):
    ordered = sorted(Decimal(str(value)) for value in values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * Decimal(str(percentile))
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _weight_band(value):
    weight = _decimal(value)
    if weight is None or weight <= 0:
        return "UNKNOWN"
    for limit, label in ((Decimal("0.5"), "0-0.5KG"), (1, "0.5-1KG"), (2, "1-2KG"), (5, "2-5KG"), (10, "5-10KG")):
        if weight <= limit:
            return label
    return "10KG+"


def _volume_band(dimensions):
    values = [_decimal((dimensions or {}).get(key)) for key in ("length_cm", "width_cm", "height_cm")]
    if any(value is None or value <= 0 for value in values):
        return "UNKNOWN"
    volume = values[0] * values[1] * values[2]
    if volume <= 5000:
        return "XS_0-5K_CM3"
    if volume <= 15000:
        return "S_5-15K_CM3"
    if volume <= 40000:
        return "M_15-40K_CM3"
    return "L_40K+_CM3"


def _zone(destination):
    destination = destination or {}
    prefix = str(destination.get("postal_code_prefix") or "").strip()
    city = str(destination.get("city") or "").strip()
    return (prefix[:3] or city[:3] or "UNKNOWN").upper()


def build_historical_profiles():
    """Agrupa por zona + bandas físicas + servicio; nunca usa promedio global."""
    groups = defaultdict(list)
    rows = LogisticsQuoteSnapshot.objects.filter(
        basis=LogisticsQuoteSnapshot.Basis.REALIZED_GUIDE,
        status="AVAILABLE",
        amount__isnull=False,
    ).order_by("id")
    rejected = 0
    for row in rows:
        weight_band = _weight_band(row.weight_kg)
        volume_band = _volume_band(row.dimensions)
        zone = _zone(row.destination)
        if "UNKNOWN" in {weight_band, volume_band, zone}:
            rejected += 1
            continue
        groups[(zone, weight_band, volume_band, row.carrier or "UNSPECIFIED")].append(row.amount)

    profiles = []
    for (zone, weight_band, volume_band, carrier), amounts in sorted(groups.items()):
        p75 = _percentile(amounts, Decimal("0.75"))
        profiles.append({
            "family": "UNRESOLVED",
            "size": f"{weight_band} · {volume_band}",
            "provider": "UNRESOLVED",
            "origin_warehouse": "UNRESOLVED",
            "destination_zone": zone,
            "weight_band": weight_band,
            "volume_band": volume_band,
            "carrier_service": carrier,
            "sample_size": len(amounts),
            "median_cop": Decimal(str(median(amounts))).quantize(Decimal("1")),
            "conservative_p75_cop": p75.quantize(Decimal("1")),
            "basis": "REALIZED_GUIDE_LOCAL_SNAPSHOT",
            "classification": "ESTIMATED",
            "assignable": False,
            "blockers": [
                "HISTORICAL_GUIDE_WITHOUT_PRODUCT_FAMILY",
                "HISTORICAL_GUIDE_WITHOUT_PROVIDER_ORIGIN",
            ] + (["SAMPLE_BELOW_3"] if len(amounts) < 3 else []),
        })
    return profiles, rows.count(), rejected


def protected_margin_preview(price, cost, estimated_shipping, minimum_margin_percent=20):
    """Evalúa protección; faltantes bloquean y nunca se convierten en cero."""
    price, cost, shipping = map(_decimal, (price, cost, estimated_shipping))
    if any(value is None for value in (price, cost, shipping)) or price <= 0:
        return {"status": "BLOCKED", "margin_percent": None, "blockers": ["PRICE_COST_OR_ESTIMATE_UNKNOWN"]}
    margin = ((price - cost - shipping) / price) * 100
    return {
        "status": "PROTECTED" if margin >= Decimal(str(minimum_margin_percent)) else "BLOCKED",
        "margin_percent": margin.quantize(Decimal("0.01")),
        "blockers": [] if margin >= Decimal(str(minimum_margin_percent)) else ["ESTIMATED_SHIPPING_BREAKS_MARGIN"],
    }


def _package_facts(variant):
    approved = {}
    candidates = variant.physical_candidates.all()
    for candidate in candidates:
        if candidate.scope != "PACKAGE" or candidate.classification != "CONFIRMED" or candidate.conflict:
            continue
        if any(decision.action == "APPROVE_LOCAL" for decision in candidate.decisions.all()):
            approved[candidate.field] = candidate.normalized_value
    dimensions = {"length_cm": approved.get("LENGTH"), "width_cm": approved.get("WIDTH"), "height_cm": approved.get("HEIGHT")}
    return approved.get("WEIGHT"), dimensions


def build_measurement_priority(limit=None):
    variants = ProductVariant.objects.select_related("product", "canonical_cost__observation").prefetch_related(
        "channel_snapshots", "inventory_sources",
        Prefetch("physical_candidates", queryset=PhysicalEvidenceCandidate.objects.prefetch_related("decisions")),
    ).filter(supplier_matches__status="EXACT", supplier_matches__supplier_item__provider__name="Barú").distinct()
    output = []
    for variant in variants:
        product = variant.product
        weight, dimensions = _package_facts(variant)
        missing = []
        if weight is None or weight <= 0:
            missing.append("PESO_PAQUETE")
        for key, label in (("length_cm", "LARGO_PAQUETE"), ("width_cm", "ANCHO_PAQUETE"), ("height_cm", "ALTO_PAQUETE")):
            if dimensions[key] is None or dimensions[key] <= 0:
                missing.append(label)
        if not missing:
            continue
        snapshots = list(variant.channel_snapshots.all())
        shopify = next((row for row in snapshots if row.channel == Channel.SHOPIFY), None)
        origins = [row.warehouse_name for row in variant.inventory_sources.all() if row.warehouse_name and not row.stock_unknown]
        cost = getattr(getattr(variant, "canonical_cost", None), "observation", None)
        canonical_cost = cost.raw_cost if cost else variant.provider_cost
        score = 0
        reasons = []
        if shopify:
            score += 25
            reasons.append("SKU exacto en snapshot Shopify")
        if product.status == "ACTIVE":
            score += 15
            reasons.append("producto activo")
        price = variant.price
        if price is not None and price >= 500000:
            score += 25
            reasons.append("alto valor")
        elif price is not None and price >= 200000:
            score += 15
            reasons.append("valor medio-alto")
        elif price is not None and price > 0:
            score += 8
        if canonical_cost is not None:
            score += 10
        if "PESO_PAQUETE" in missing:
            score += 8
        if len(missing) >= 3:
            score += 12
        if not origins:
            score += 10
            reasons.append("bodega/origen desconocido")
        quality_risk = max(0, 100 - int(product.quality_score or 0))
        score += min(10, (quality_risk + 9) // 10)
        output.append({
            "priority_score": int(score),
            "sku": variant.sku,
            "product": product.title,
            "family": product.category or product.product_type or "SIN_FAMILIA",
            "provider": product.vendor or "UNKNOWN",
            "origin_warehouse": ", ".join(sorted(set(origins))) or "UNKNOWN",
            "price_cop": price,
            "canonical_cost_cop": canonical_cost,
            "quality_score": product.quality_score,
            "channel_count": len(snapshots),
            "shopify_exists": bool(shopify),
            "missing_package_fields": missing,
            "estimated_profile_assignable": False,
            "estimate_blockers": ["PACKAGE_BAND_UNKNOWN", "HISTORICAL_FAMILY_ORIGIN_UNRESOLVED"],
            "priority_reasons": reasons,
            "recommended_action": "Medir empaque completo y confirmar bodega de despacho",
        })
    output.sort(key=lambda row: (-row["priority_score"], row["sku"] or "", row["product"]))
    return output[:limit] if limit else output


def build_phase7_workspace(priority_limit=100):
    profiles, guide_count, rejected = build_historical_profiles()
    all_priorities = build_measurement_priority()
    priorities = all_priorities[:priority_limit]
    locally_connected = {
        Channel.SHOPIFY: ChannelSnapshot.objects.filter(channel=Channel.SHOPIFY).exclude(state="STALE").exists(),
        Channel.MERCADO_LIBRE: ExternalChannelProductSnapshot.objects.filter(channel=Channel.MERCADO_LIBRE, active=True).exists(),
        Channel.FALABELLA: ExternalChannelProductSnapshot.objects.filter(channel=Channel.FALABELLA, active=True).exists(),
        Channel.MADECENTRO: ExternalChannelProductSnapshot.objects.filter(channel=Channel.MADECENTRO, active=True).exists(),
    }
    return {
        "mode": "STRICT_LOCAL_PHASE_7",
        "channels": [{
            **row,
            "implementation": "IMPLEMENTED_LOCAL" if locally_connected.get(row["code"], False) else row["implementation"],
            "connection": "LOCAL_SNAPSHOT" if locally_connected.get(row["code"], False) else row["connection"],
            "adapter": "snapshot local de solo lectura" if locally_connected.get(row["code"], False) else row["adapter"],
            "capabilities": {
                "existence": "LOCAL" if locally_connected.get(row["code"], False) else "PREPARED",
                "quality_completeness": "LOCAL" if locally_connected.get(row["code"], False) else "PREPARED",
                "price": "LOCAL_IF_SNAPSHOT" if locally_connected.get(row["code"], False) else "PREPARED",
                "inventory": "LOCAL_IF_SNAPSHOT" if locally_connected.get(row["code"], False) else "PREPARED",
                "cost_shipping": "LOCAL_IF_SOURCE" if locally_connected.get(row["code"], False) else "PREPARED",
                "future_actions": "BLOCKED_NO_EXTERNAL_WRITES",
            },
        } for row in CHANNEL_MATRIX],
        "logistics": {
            "classification": "ESTIMATED",
            "realized_guides": guide_count,
            "rejected_incomplete_guides": rejected,
            "profiles": profiles,
            "assignable_profiles": sum(1 for row in profiles if row["assignable"]),
            "aggregation": "mediana y percentil 75 por zona + banda de peso + banda de volumen + servicio",
            "global_average_forbidden": True,
            "commercial_quote": False,
            "learning": "La reconstrucción incorpora automáticamente nuevos snapshots REALIZED_GUIDE sanitizados.",
            "margin_contract": "precio - costo - estimado; bloquea si el margen queda bajo el mínimo",
        },
        "measurement_priority": priorities,
        "measurement_priority_total": len(all_priorities),
        "siigo_architecture_decision": {
            "status": "FUTURE_SEPARATE_MODULE",
            "navigation_and_permissions": "propias",
            "shared_internal_contracts": ["catálogo maestro", "clientes", "pedidos", "costos", "trazabilidad"],
            "required_gates": ["vista previa", "aprobación humana", "consecutivos e impuestos", "idempotencia", "conciliación", "nota crédito/anulación", "auditoría"],
            "separation": "crear/sincronizar producto Siigo no equivale a emitir factura",
            "code_implemented": False,
        },
        "external_writes": 0,
        "execution_allowed_external": False,
    }
