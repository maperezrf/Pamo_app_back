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


VOLUMETRIC_PRODUCT_FAMILIES = (
    ("LAVAPLATOS_VOLUMINOSO", "lavaplat"),
    ("LAVAMANOS_VOLUMINOSO", "lavaman"),
)
VOLUMETRIC_ACCESSORY_TERMS = (
    "acoflex",
    "aireador",
    "canastilla",
    "cartridge",
    "combinación",
    "combinacion",
    "cuello",
    "desagüe",
    "desague",
    "dispensador",
    "grifería",
    "griferia",
    "grifer¡a",
    "grifo",
    "llave",
    "manguera",
    "manilla",
    "mezclador",
    "monomando",
    "monocontrol",
    "pico",
    "registro",
    "regadera",
    "repuesto",
    "set de lujo para",
    "set para",
    "sifón",
    "sifon",
    "válvula",
    "valvula",
)


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


def billable_weight_kg(weight_kg, dimensions, volumetric_divisor=Decimal("5000")):
    """Referencia neutral; cada transportadora conserva su propio divisor real."""
    weight = _decimal(weight_kg)
    values = [_decimal((dimensions or {}).get(key)) for key in ("length_cm", "width_cm", "height_cm")]
    volumetric = None
    if all(value is not None and value > 0 for value in values):
        volumetric = (values[0] * values[1] * values[2]) / volumetric_divisor
    candidates = [value for value in (weight, volumetric) if value is not None and value > 0]
    return max(candidates) if candidates else None


def product_shipping_family(variant):
    """Detecta piezas voluminosas sin confundirlas con griferías o accesorios."""
    product = variant.product
    # El título y el tipo describen el artículo. La categoría es demasiado
    # amplia (p. ej. una grifería puede pertenecer a "lavamanos") y no prueba
    # que el producto sea la pieza voluminosa.
    title = str(product.title or "").casefold()
    product_type = str(product.product_type or "").casefold()
    type_family_positions = [
        product_type.find(term)
        for _, term in VOLUMETRIC_PRODUCT_FAMILIES
        if product_type.find(term) >= 0
    ]
    first_type_family = min(type_family_positions) if type_family_positions else None
    first_type_accessory = min(
        (position for position in (product_type.find(term) for term in VOLUMETRIC_ACCESSORY_TERMS) if position >= 0),
        default=None,
    )
    if first_type_accessory is not None and (
        first_type_family is None or first_type_accessory < first_type_family
    ):
        return None
    searchable_fields = (title, product_type)
    for family, term in VOLUMETRIC_PRODUCT_FAMILIES:
        for searchable in searchable_fields:
            family_position = searchable.find(term)
            if family_position < 0:
                continue
            prefix = searchable[:family_position]
            if not any(accessory in prefix for accessory in VOLUMETRIC_ACCESSORY_TERMS):
                return family
    return None


def _complete_dimensions(dimensions):
    values = [_decimal((dimensions or {}).get(key)) for key in ("length_cm", "width_cm", "height_cm")]
    return all(value is not None and value > 0 for value in values)


def shipping_tariff_band(weight_kg, dimensions):
    billable = billable_weight_kg(weight_kg, dimensions)
    if billable is None:
        return "SIN_DATOS"
    for limit, label in (
        (Decimal("1"), "HASTA_1_KG"),
        (Decimal("2"), "1_A_2_KG"),
        (Decimal("5"), "2_A_5_KG"),
        (Decimal("10"), "5_A_10_KG"),
    ):
        if billable <= limit:
            return label
    return "MAS_DE_10_KG"


def _rounded_cop(value, increment=Decimal("500")):
    return (Decimal(value) / increment).quantize(Decimal("1")) * increment


def _trimmed_mean(values):
    ordered = sorted(Decimal(value) for value in values if value is not None and Decimal(value) > 0)
    if not ordered:
        return None
    trim = int(len(ordered) * Decimal("0.05")) if len(ordered) >= 20 else 0
    selected = ordered[trim:len(ordered) - trim] if trim else ordered
    return sum(selected, Decimal("0")) / len(selected)


def build_average_shipping_reference():
    """Promedio informativo, ponderado por frecuencia real de guías y sin duplicados."""
    rows = LogisticsQuoteSnapshot.objects.filter(
        provider="ENVIA",
        basis=LogisticsQuoteSnapshot.Basis.REALIZED_GUIDE,
        status="AVAILABLE",
        amount__isnull=False,
        amount__gt=0,
    ).select_related("variant__product").order_by("-observed_at", "-id")
    unique = []
    seen = set()
    for row in rows:
        identity = row.external_reference_hash or row.fingerprint
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(row)
    amounts = [row.amount for row in unique]
    overall = _trimmed_mean(amounts)
    if overall is None:
        return None
    grouped = defaultdict(list)
    zones = defaultdict(list)
    product_families = defaultdict(list)
    for row in unique:
        grouped[shipping_tariff_band(row.weight_kg, row.dimensions)].append(row.amount)
        zones[_zone(row.destination)].append(row.amount)
        if row.variant_id:
            family = product_shipping_family(row.variant)
            if family:
                product_families[family].append(row.amount)
    overall_rounded = _rounded_cop(overall)
    reliable_large_floor_values = grouped.get("5_A_10_KG") or []
    reliable_large_floor = (
        _rounded_cop(_trimmed_mean(reliable_large_floor_values))
        if len(reliable_large_floor_values) >= 5
        else overall_rounded
    )
    bands = {}
    for band, band_amounts in sorted(grouped.items()):
        raw = _trimmed_mean(band_amounts)
        sparse = len(band_amounts) < 5
        if not sparse and raw is not None:
            amount = _rounded_cop(raw)
            fallback_basis = None
        elif band == "MAS_DE_10_KG":
            amount = max(overall_rounded, reliable_large_floor)
            fallback_basis = "CONSERVATIVE_5_A_10_KG_FLOOR"
        else:
            amount = overall_rounded
            fallback_basis = "GLOBAL_HISTORY_FALLBACK"
        bands[band] = {
            "amount": amount,
            "sample_size": len(band_amounts),
            "uses_global_fallback": sparse,
            "fallback_basis": fallback_basis,
        }
    top_zones = sorted(zones.items(), key=lambda item: (-len(item[1]), item[0]))[:5]
    return {
        "amount": overall_rounded,
        "currency": "COP",
        "sample_size": len(unique),
        "bands": bands,
        "product_families": {
            family: {
                "amount": _rounded_cop(_percentile(family_amounts, Decimal("0.75"))),
                "sample_size": len(family_amounts),
                "basis": "REALIZED_GUIDE_PRODUCT_FAMILY_P75",
            }
            for family, family_amounts in sorted(product_families.items())
            if len(family_amounts) >= 5
        },
        "top_destination_zones": [
            {
                "zone": zone,
                "shipments": len(zone_amounts),
                "share_percent": round(len(zone_amounts) * 100 / len(unique), 1),
                "average_amount": _rounded_cop(_trimmed_mean(zone_amounts)),
            }
            for zone, zone_amounts in top_zones
        ],
        "basis": "REALIZED_GUIDE_FREQUENCY_WEIGHTED_TRIMMED_MEAN",
        "classification": "ESTIMATED_INFORMATIONAL_ONLY",
        "volumetric_divisor_reference": 5000,
    }


def approved_package_values(variant):
    candidates = getattr(variant, "envia_physical_candidates", None)
    if candidates is None:
        candidates = variant.physical_candidates.filter(
            scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
            classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
            conflict=False,
        ).prefetch_related("decisions")
    values = {}
    for candidate in candidates:
        latest = next(iter(candidate.decisions.all()), None)
        if latest and latest.action == "APPROVE_LOCAL":
            values[candidate.field] = candidate.normalized_value
    sources = ["CONFIRMED_APPROVED_PACKAGE"] if values else []
    snapshots = getattr(variant, "channel_snapshots", None)
    snapshots = list(snapshots.all()) if hasattr(snapshots, "all") else []
    shopify = next((row for row in snapshots if row.channel == Channel.SHOPIFY), None)
    if shopify:
        payload = shopify.payload or {}
        native_weight = payload.get("weight") or {}
        native_value = _decimal(native_weight.get("value")) if isinstance(native_weight, dict) else None
        if native_value and native_value > 0:
            unit = str(native_weight.get("unit") or "KILOGRAMS").upper()
            if unit in {"GRAMS", "G"}:
                native_value /= Decimal("1000")
            elif unit in {"POUNDS", "LB"}:
                native_value *= Decimal("0.45359237")
            inserted = PhysicalEvidenceCandidate.Field.WEIGHT not in values
            values.setdefault(PhysicalEvidenceCandidate.Field.WEIGHT, native_value)
            if inserted and "SHOPIFY_NATIVE_WEIGHT" not in sources:
                sources.append("SHOPIFY_NATIVE_WEIGHT")
        metafield_map = {
            "peso_empacado": PhysicalEvidenceCandidate.Field.WEIGHT,
            "largo_paquete": PhysicalEvidenceCandidate.Field.LENGTH,
            "ancho_paquete": PhysicalEvidenceCandidate.Field.WIDTH,
            "alto_paquete": PhysicalEvidenceCandidate.Field.HEIGHT,
        }
        for metafield in payload.get("variantMetafields") or []:
            field = metafield_map.get(str(metafield.get("key") or ""))
            raw = metafield.get("jsonValue") or {}
            value = _decimal(raw.get("value")) if isinstance(raw, dict) else None
            if not field or value is None or value <= 0:
                continue
            unit = str(raw.get("unit") or "").upper()
            if field == PhysicalEvidenceCandidate.Field.WEIGHT and unit in {"GRAMS", "G"}:
                value /= Decimal("1000")
            elif field != PhysicalEvidenceCandidate.Field.WEIGHT and unit in {"METERS", "M"}:
                value *= Decimal("100")
            elif field != PhysicalEvidenceCandidate.Field.WEIGHT and unit in {"MILLIMETERS", "MM"}:
                value /= Decimal("10")
            inserted = field not in values
            values.setdefault(field, value)
            if inserted and "SHOPIFY_LOGISTICS_METAFIELDS" not in sources:
                sources.append("SHOPIFY_LOGISTICS_METAFIELDS")
    return {
        "weight_kg": values.get(PhysicalEvidenceCandidate.Field.WEIGHT),
        "dimensions": {
            "length_cm": values.get(PhysicalEvidenceCandidate.Field.LENGTH),
            "width_cm": values.get(PhysicalEvidenceCandidate.Field.WIDTH),
            "height_cm": values.get(PhysicalEvidenceCandidate.Field.HEIGHT),
        },
        "source": "+".join(sources) or "NO_PACKAGE_DATA",
    }


def average_shipping_for_variant(variant, reference=None):
    reference = reference or build_average_shipping_reference()
    if not reference:
        return None
    package = approved_package_values(variant)
    family = product_shipping_family(variant)
    family_reference = (reference.get("product_families") or {}).get(family)
    package_complete = _complete_dimensions(package["dimensions"])
    if family_reference:
        return {
            **reference,
            "amount": family_reference["amount"],
            "basis": family_reference["basis"],
            "tariff_band": family,
            "package_basis": (
                package["source"] + "+PRODUCT_FAMILY_HISTORY_P75"
                if package_complete
                else "PRODUCT_FAMILY_HISTORY_FALLBACK"
            ),
            "band_sample_size": family_reference["sample_size"],
            "uses_global_fallback": False,
            "assumed": not package_complete,
            "requires_review": not package_complete,
            "classification_reason": (
                "P75 histórico de guías reales de la familia; el empaque está completo, pero la tarifa exacta depende del destino y servicio."
                if package_complete
                else "P75 histórico de guías reales de la familia; faltan medidas completas del empaque."
            ),
        }
    band = shipping_tariff_band(package["weight_kg"], package["dimensions"])
    if band == "SIN_DATOS":
        assumed_reference = (reference.get("bands") or {}).get("HASTA_1_KG")
        assumed_amount = assumed_reference["amount"] if assumed_reference else reference["amount"]
        return {
            **reference,
            "amount": assumed_amount,
            "basis": "USER_POLICY_DEFAULT_UNDER_1KG_WITH_HISTORICAL_RATE",
            "tariff_band": "HASTA_1_KG_ASUMIDO",
            "package_basis": "USER_POLICY_DEFAULT_UNDER_1KG",
            "band_sample_size": assumed_reference["sample_size"] if assumed_reference else 0,
            "uses_global_fallback": assumed_reference is None,
            "assumed": True,
            "requires_review": True,
            "classification_reason": "Sin evidencia física; política provisional para productos pequeños.",
        }
    band_reference = (reference.get("bands") or {}).get(band)
    amount = band_reference["amount"] if band_reference else reference["amount"]
    sparse_history = not band_reference or band_reference.get("uses_global_fallback", False)
    return {
        **reference,
        "amount": amount,
        "basis": "REALIZED_GUIDE_TARIFF_BAND_TRIMMED_MEAN",
        "tariff_band": band,
        "package_basis": package["source"] if band != "SIN_DATOS" else "GLOBAL_HISTORY_FALLBACK",
        "band_sample_size": band_reference["sample_size"] if band_reference else 0,
        "uses_global_fallback": not band_reference or band_reference.get("uses_global_fallback", False),
        "assumed": False,
        "requires_review": not package_complete or sparse_history,
        "classification_reason": (
            "Historial insuficiente en la banda; se conserva una referencia conservadora y requiere cotización actual."
            if sparse_history
            else "Peso facturable calculado con peso y dimensiones completos."
            if package_complete
            else "Banda basada en el peso disponible; faltan medidas del empaque."
        ),
    }


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
