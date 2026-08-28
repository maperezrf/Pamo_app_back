import hashlib
import html
import json
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    ChannelSnapshot,
    PhysicalEnrichmentPilotSelection,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    ProductMetafield,
    ShopifyPhysicalUpdatePreview,
)


DIMENSION_UNITS = {"MM": Decimal("0.1"), "CM": Decimal("1"), "M": Decimal("100"), "IN": Decimal("2.54"), "PULG": Decimal("2.54"), "FT": Decimal("30.48"), "YD": Decimal("91.44")}
WEIGHT_UNITS = {"G": Decimal("0.001"), "GR": Decimal("0.001"), "KG": Decimal("1"), "LB": Decimal("0.45359237"), "OZ": Decimal("0.028349523")}
# ``\b`` no detecta ``largo_paquete`` porque ``_`` cuenta como carácter de
# palabra. Los metacampos usan con frecuencia guiones bajos o puntos, así que
# delimitamos únicamente contra letras y números.
PACKAGE_WORDS = re.compile(
    r"(?<![A-Za-zÁÉÍÓÚáéíóú0-9])(paquete|empaque|embalaje|caja|bulto|despacho|env[ií]o)(?![A-Za-zÁÉÍÓÚáéíóú0-9])",
    re.I,
)
DIMENSION_WORDS = re.compile(r"\b(dimensi(?:o|ó)nes?|largo|ancho|alto|profundidad|tama(?:n|ñ)o)\b", re.I)


class PhysicalValidationError(ValueError):
    pass


def plain_text(value):
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def normalize_measurement(field, value, unit):
    if field not in PhysicalEvidenceCandidate.Field.values:
        raise PhysicalValidationError("Campo físico no permitido.")
    try:
        raw = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PhysicalValidationError("El valor no es numérico.") from error
    if raw <= 0:
        raise PhysicalValidationError("El valor físico debe ser positivo.")
    normalized_unit = str(unit or "").strip().upper().replace("KILOGRAMS", "KG").replace("GRAMS", "G")
    normalized_unit = normalized_unit.replace("POUNDS", "LB").replace("OUNCES", "OZ")
    normalized_unit = normalized_unit.replace("CENTIMETERS", "CM").replace("MILLIMETERS", "MM").replace("METERS", "M")
    normalized_unit = normalized_unit.replace("INCHES", "IN").replace("PULGADAS", "PULG").replace("FEET", "FT").replace("YARDS", "YD")
    if field == PhysicalEvidenceCandidate.Field.WEIGHT:
        if normalized_unit not in WEIGHT_UNITS:
            raise PhysicalValidationError("Unidad de peso no permitida.")
        result = raw * WEIGHT_UNITS[normalized_unit]
        if result > Decimal("500"):
            raise PhysicalValidationError("Peso absurdo: supera 500 kg.")
        return result.quantize(Decimal("0.0001")), "KG"
    if normalized_unit not in DIMENSION_UNITS:
        raise PhysicalValidationError("Unidad de dimensión no permitida.")
    result = raw * DIMENSION_UNITS[normalized_unit]
    if result > Decimal("500"):
        raise PhysicalValidationError("Dimensión absurda: supera 500 cm.")
    return result.quantize(Decimal("0.0001")), "CM"


def candidate_fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@transaction.atomic
def upsert_candidate(*, variant, supplier_item, field, scope, classification, source_type,
                     source_reference, evidence_excerpt, original_value, original_unit,
                     confidence, source_url="", identifier_type="", identifier_value="",
                     selector="", extraction_method="REGEX_V1", observed_at=None, stale_after=None):
    if scope not in PhysicalEvidenceCandidate.Scope.values:
        raise PhysicalValidationError("Alcance físico no permitido.")
    if classification not in PhysicalEvidenceCandidate.Classification.values:
        raise PhysicalValidationError("Clasificación de evidencia no permitida.")
    try:
        confidence = Decimal(str(confidence))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PhysicalValidationError("Confianza inválida.") from error
    if confidence < 0 or confidence > 1:
        raise PhysicalValidationError("La confianza debe estar entre 0 y 1.")
    if not str(evidence_excerpt or "").strip():
        raise PhysicalValidationError("La evidencia debe incluir un fragmento mínimo.")
    normalized_value, normalized_unit = normalize_measurement(field, original_value, original_unit)
    original_decimal = Decimal(str(original_value).replace(",", "."))
    payload = {
        "variant": str(variant.id) if variant else None, "supplier_item": str(supplier_item.id) if supplier_item else None,
        "field": field, "scope": scope, "source_type": source_type, "source_reference": source_reference,
        "source_url": source_url, "identifier_type": identifier_type, "identifier_value": identifier_value,
        "selector": selector, "excerpt": evidence_excerpt[:700], "original_value": str(original_value),
        "original_unit": original_unit, "normalized_value": str(normalized_value), "normalized_unit": normalized_unit,
    }
    fingerprint = candidate_fingerprint(payload)
    candidate, _ = PhysicalEvidenceCandidate.objects.update_or_create(
        content_fingerprint=fingerprint,
        defaults={
            "variant": variant, "supplier_item": supplier_item, "field": field, "scope": scope,
            "classification": classification, "source_type": source_type, "source_url": source_url,
            "source_reference": source_reference, "matching_identifier_type": identifier_type,
            "matching_identifier_value": identifier_value, "evidence_excerpt": evidence_excerpt[:700],
            "evidence_selector": selector, "observed_at": observed_at or timezone.now(),
            "extraction_method": extraction_method, "original_value": original_decimal,
            "original_unit": str(original_unit).upper(), "normalized_value": normalized_value,
            "normalized_unit": normalized_unit, "confidence": confidence, "stale_after": stale_after,
            "external_writes": 0,
        },
    )
    peers = PhysicalEvidenceCandidate.objects.filter(
        variant=variant, supplier_item=supplier_item, field=field, scope=scope,
    ).exclude(pk=candidate.pk)
    conflicts = []
    for peer in peers:
        larger = max(peer.normalized_value, normalized_value)
        if larger and abs(peer.normalized_value - normalized_value) / larger > Decimal("0.20"):
            conflicts.append({"candidate_id": peer.id, "value": str(peer.normalized_value), "source": peer.source_reference})
            if not peer.conflict:
                peer.conflict = True
                peer.conflict_details = {"reason": "DIFFERENCE_OVER_20_PERCENT", "against": str(candidate.id)}
                peer.save(update_fields=["conflict", "conflict_details"])
    cross_scope_peers = PhysicalEvidenceCandidate.objects.filter(
        variant=variant, supplier_item=supplier_item, field=field,
    ).exclude(scope=scope).exclude(pk=candidate.pk)
    for peer in cross_scope_peers:
        package = candidate if scope == PhysicalEvidenceCandidate.Scope.PACKAGE else peer
        product = peer if scope == PhysicalEvidenceCandidate.Scope.PACKAGE else candidate
        if package.scope == PhysicalEvidenceCandidate.Scope.PACKAGE and product.scope == PhysicalEvidenceCandidate.Scope.PRODUCT and package.normalized_value < product.normalized_value * Decimal("0.95"):
            conflicts.append({"candidate_id": peer.id, "value": str(peer.normalized_value), "source": peer.source_reference, "reason": "PACKAGE_SMALLER_THAN_PRODUCT"})
            peer.conflict = True
            peer.conflict_details = {"reason": "PACKAGE_SMALLER_THAN_PRODUCT", "against": str(candidate.id)}
            peer.save(update_fields=["conflict", "conflict_details"])
    candidate.conflict = bool(conflicts)
    candidate.conflict_details = {"reason": "PHYSICAL_EVIDENCE_CONFLICT", "peers": conflicts} if conflicts else {}
    candidate.save(update_fields=["conflict", "conflict_details"])
    return candidate


def description_measurements(text):
    source = plain_text(text)
    findings = []
    label_map = {
        "largo": PhysicalEvidenceCandidate.Field.LENGTH,
        "ancho": PhysicalEvidenceCandidate.Field.WIDTH,
        "alto": PhysicalEvidenceCandidate.Field.HEIGHT,
        "profundidad": PhysicalEvidenceCandidate.Field.LENGTH,
        "peso": PhysicalEvidenceCandidate.Field.WEIGHT,
    }
    labeled = re.compile(
        r"(?P<label>largo|ancho|alto|profundidad|peso)\s*(?:del\s+(?P<target>paquete|empaque|producto))?\s*[:=\-]?\s*(?P<value>\d+(?:[\.,]\d+)?)\s*(?P<unit>kg|gr?|lb|oz|mm|cm|m|pulg)", re.I
    )
    for match in labeled.finditer(source):
        start, end = max(0, match.start() - 55), min(len(source), match.end() + 55)
        excerpt = source[start:end]
        # No promovemos a PACKAGE por una palabra cercana: el texto debe ligar
        # explícitamente el campo con "paquete" o "empaque".
        scope = PhysicalEvidenceCandidate.Scope.PACKAGE if match.group("target") in {"paquete", "empaque"} else PhysicalEvidenceCandidate.Scope.PRODUCT
        findings.append({
            "field": label_map[match.group("label").lower()], "scope": scope,
            "value": match.group("value"), "unit": match.group("unit"), "excerpt": excerpt,
            "selector": f"description:{match.start()}-{match.end()}",
        })
    triple = re.compile(
        r"(?P<context>(?:dimensiones?|medidas?|tama(?:n|ñ)o)(?:\s+del?\s+(?:paquete|empaque|producto))?[^\d]{0,25})"
        r"(?P<a>\d+(?:[\.,]\d+)?)\s*[x×]\s*(?P<b>\d+(?:[\.,]\d+)?)\s*[x×]\s*(?P<c>\d+(?:[\.,]\d+)?)\s*(?P<unit>mm|cm|m|pulg)", re.I
    )
    for match in triple.finditer(source):
        start, end = max(0, match.start() - 35), min(len(source), match.end() + 35)
        excerpt = source[start:end]
        scope = PhysicalEvidenceCandidate.Scope.PACKAGE if PACKAGE_WORDS.search(match.group("context")) else PhysicalEvidenceCandidate.Scope.PRODUCT
        for field, group in (("LENGTH", "a"), ("WIDTH", "b"), ("HEIGHT", "c")):
            findings.append({"field": field, "scope": scope, "value": match.group(group), "unit": match.group("unit"), "excerpt": excerpt, "selector": f"description:{match.start()}-{match.end()}"})
    return findings


def metafield_measurements(metafields):
    findings = []
    field_keys = {
        "peso": "WEIGHT", "weight": "WEIGHT", "largo": "LENGTH", "length": "LENGTH",
        "ancho": "WIDTH", "width": "WIDTH", "alto": "HEIGHT", "height": "HEIGHT",
    }
    for meta in metafields:
        key = f"{meta.namespace}.{meta.key}".lower()
        field = next((value for token, value in field_keys.items() if token in key), None)
        if not field:
            continue
        value_text = plain_text(meta.value).strip('"')
        match = re.search(r"(?P<value>\d+(?:[\.,]\d+)?)\s*(?P<unit>kg|gr?|lb|oz|mm|cm|m|pulg)\b", value_text, re.I)
        if not match:
            continue
        explicit_package = bool(PACKAGE_WORDS.search(key))
        findings.append({
            "field": field, "scope": "PACKAGE" if explicit_package else "PRODUCT",
            "value": match.group("value"), "unit": match.group("unit"),
            "excerpt": f"{meta.namespace}.{meta.key}={value_text}"[:700],
            "selector": f"metafield:{meta.namespace}.{meta.key}", "explicit_package": explicit_package,
        })
    return findings


def snapshot_variant_metafield_measurements(payload):
    findings = []
    field_keys = {
        "peso": "WEIGHT", "weight": "WEIGHT", "largo": "LENGTH", "length": "LENGTH",
        "ancho": "WIDTH", "width": "WIDTH", "alto": "HEIGHT", "height": "HEIGHT",
    }
    for meta in (payload or {}).get("variantMetafields") or []:
        namespace = str(meta.get("namespace") or "")
        meta_key = str(meta.get("key") or "")
        key = f"{namespace}.{meta_key}".lower()
        field = next((value for token, value in field_keys.items() if token in key), None)
        if not field:
            continue
        raw = meta.get("jsonValue")
        if isinstance(raw, dict):
            value, unit = raw.get("value"), raw.get("unit")
        else:
            match = re.search(r"(?P<value>\d+(?:[\.,]\d+)?)\s*(?P<unit>kg|gr?|lb|oz|mm|cm|m|pulg)\b", plain_text(raw), re.I)
            value, unit = (match.group("value"), match.group("unit")) if match else (None, None)
        if value in (None, "") or not unit:
            continue
        explicit_package = bool(PACKAGE_WORDS.search(key))
        findings.append({
            "field": field, "scope": "PACKAGE" if explicit_package else "PRODUCT",
            "value": value, "unit": unit,
            "excerpt": f"{namespace}.{meta_key}={json.dumps(raw, ensure_ascii=False, default=str)}"[:700],
            "selector": f"variantMetafields:{namespace}.{meta_key}",
            "explicit_package": explicit_package, "observed_at": parse_datetime(str(meta.get("updatedAt") or "")),
        })
    return findings


def append_valid_candidate(created, **kwargs):
    """Conserva el lote aunque una fuente real traiga cero o una unidad inválida."""
    try:
        created.append(upsert_candidate(**kwargs))
    except PhysicalValidationError:
        return False
    return True


@transaction.atomic
def analyze_local_item(item, variant):
    created = []
    snapshot = ChannelSnapshot.objects.filter(variant=variant, channel="SHOPIFY").first()
    weight = (((snapshot.payload or {}).get("weight") or {}) if snapshot else {})
    if weight.get("value") is not None and weight.get("unit"):
        append_valid_candidate(
            created,
            variant=variant, supplier_item=item, field="WEIGHT", scope="PACKAGE", classification="CONFIRMED",
            source_type="SHOPIFY_STRUCTURED", source_reference="Shopify inventoryItem.measurement.weight",
            evidence_excerpt=f"requiresShipping={bool((snapshot.payload or {}).get('requiresShipping'))}; weight={weight.get('value')} {weight.get('unit')}",
            original_value=weight["value"], original_unit=weight["unit"], confidence=Decimal("0.9900"),
            identifier_type="SHOPIFY_VARIANT_ID", identifier_value=variant.shopify_variant_id,
            selector="inventoryItem.measurement.weight", extraction_method="STRUCTURED_FIELD_V1",
            observed_at=snapshot.observed_at or timezone.now(),
        )
    for finding in snapshot_variant_metafield_measurements((snapshot.payload or {}) if snapshot else {}):
        append_valid_candidate(
            created,
            variant=variant, supplier_item=item, field=finding["field"], scope=finding["scope"],
            classification="CONFIRMED" if finding["explicit_package"] else "DERIVED",
            source_type="SHOPIFY_METAFIELD", source_reference="Shopify variant metafield",
            evidence_excerpt=finding["excerpt"], original_value=finding["value"], original_unit=finding["unit"],
            confidence=Decimal("0.9900") if finding["explicit_package"] else Decimal("0.8500"),
            identifier_type="SHOPIFY_VARIANT_ID", identifier_value=variant.shopify_variant_id,
            selector=finding["selector"], extraction_method="VARIANT_METAFIELD_SEMANTIC_V1",
            observed_at=finding["observed_at"] or (snapshot.observed_at if snapshot else timezone.now()),
        )
    for finding in metafield_measurements(ProductMetafield.objects.filter(product=variant.product)):
        append_valid_candidate(
            created,
            variant=variant, supplier_item=item, field=finding["field"], scope=finding["scope"],
            classification="CONFIRMED" if finding["explicit_package"] else "DERIVED",
            source_type="SHOPIFY_METAFIELD", source_reference="Shopify product metafield",
            evidence_excerpt=finding["excerpt"], original_value=finding["value"], original_unit=finding["unit"],
            confidence=Decimal("0.9800") if finding["explicit_package"] else Decimal("0.8500"),
            identifier_type="SHOPIFY_PRODUCT_ID", identifier_value=variant.product.shopify_product_id or "",
            selector=finding["selector"], extraction_method="METAFIELD_SEMANTIC_V1", observed_at=variant.product.updated_at,
        )
    for finding in description_measurements(variant.product.description_html):
        append_valid_candidate(
            created,
            variant=variant, supplier_item=item, field=finding["field"], scope=finding["scope"],
            classification="DERIVED", source_type="SHOPIFY_DESCRIPTION",
            source_reference="Shopify product description_html", evidence_excerpt=finding["excerpt"],
            original_value=finding["value"], original_unit=finding["unit"], confidence=Decimal("0.8200") if finding["scope"] == "PACKAGE" else Decimal("0.7200"),
            identifier_type="SHOPIFY_PRODUCT_ID", identifier_value=variant.product.shopify_product_id or "",
            selector=finding["selector"], extraction_method="LABELED_TEXT_REGEX_V1", observed_at=variant.product.updated_at,
        )
    return created


@transaction.atomic
def select_pilot(items, limit=25):
    scored = []
    for item in items:
        match = item.reconciliations.filter(status="EXACT", variant__isnull=False).select_related("variant__product").first()
        if not match:
            continue
        variant = match.variant
        description = plain_text(variant.product.description_html)
        criteria = ["SKU_EXACT_SHOPIFY", "COST_CONFIRMED", "INVENTORY_VISIBLE"]
        score = 100
        if variant.barcode:
            score += 30
            criteria.append("GTIN_AVAILABLE")
        if DIMENSION_WORDS.search(description):
            score += 25
            criteria.append("OWN_DESCRIPTION_DIMENSION_TERMS")
        if re.search(r"\d+(?:[\.,]\d+)?\s*(?:mm|cm|kg|gr?)\b", description, re.I):
            score += 20
            criteria.append("OWN_DESCRIPTION_MEASUREMENT")
        if any(row["scope"] == PhysicalEvidenceCandidate.Scope.PACKAGE for row in description_measurements(description)):
            score += 10
            criteria.append("OWN_DESCRIPTION_EXPLICIT_PACKAGE_MEASUREMENT")
        if ProductMetafield.objects.filter(product=variant.product, key__iregex=r"peso|weight|largo|length|ancho|width|alto|height|paquete|empaque").exists():
            score += 25
            criteria.append("PHYSICAL_METAFIELD")
        if PhysicalEvidenceCandidate.objects.filter(
            variant=variant,
            source_type__in=[PhysicalEvidenceCandidate.SourceType.MANUFACTURER, PhysicalEvidenceCandidate.SourceType.PUBLIC_RETAIL_EXACT],
        ).exists():
            score += 25
            criteria.append("PUBLIC_EXACT_EVIDENCE")
        scored.append((score, variant.sku, item, variant, criteria))
    scored.sort(key=lambda row: (-row[0], row[1]))
    chosen = scored[:limit]
    chosen_variant_ids = [row[3].id for row in chosen]
    PhysicalEnrichmentPilotSelection.objects.exclude(variant_id__in=chosen_variant_ids).delete()
    selected = []
    for rank, (score, _, item, variant, criteria) in enumerate(chosen, start=1):
        row, _ = PhysicalEnrichmentPilotSelection.objects.update_or_create(
            variant=variant,
            defaults={"supplier_item": item, "score": score, "criteria": criteria, "rank": rank},
        )
        selected.append(row)
    return selected


def latest_approved_candidate(variant, field):
    candidates = PhysicalEvidenceCandidate.objects.filter(
        variant=variant, field=field, scope="PACKAGE",
    ).prefetch_related("decisions").order_by("-confidence", "-observed_at")
    for candidate in candidates:
        decision = candidate.decisions.first()
        if not decision or decision.action != "APPROVE_LOCAL":
            continue
        if decision.expires_at and decision.expires_at <= timezone.now():
            continue
        if candidate.classification != PhysicalEvidenceCandidate.Classification.CONFIRMED or candidate.conflict:
            continue
        if candidate.stale_after and candidate.stale_after <= timezone.now():
            continue
        return candidate
    return None


@transaction.atomic
def build_shopify_preview(variant):
    fields = {name: latest_approved_candidate(variant, name) for name in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT")}
    blockers = [f"MISSING_APPROVED_PACKAGE_{name}" for name, candidate in fields.items() if candidate is None]
    proposed = {}
    evidence = {}
    key_map = {"WEIGHT": "package_weight_kg", "LENGTH": "package_length_cm", "WIDTH": "package_width_cm", "HEIGHT": "package_height_cm"}
    for field, candidate in fields.items():
        if not candidate:
            continue
        key = key_map[field]
        proposed[f"merci_logistics.{key}"] = str(candidate.normalized_value)
        evidence[key] = {"candidate_id": candidate.id, "source": candidate.source_reference, "classification": candidate.classification, "confidence": str(candidate.confidence)}
    snapshot = ChannelSnapshot.objects.filter(variant=variant, channel="SHOPIFY").first()
    previous = {"structured_weight": ((snapshot.payload or {}).get("weight") if snapshot else None), "metafields": {}}
    payload = {"variant": variant.shopify_variant_id, "previous": previous, "proposed": proposed, "evidence": evidence, "blockers": blockers}
    key = candidate_fingerprint(payload)
    preview, _ = ShopifyPhysicalUpdatePreview.objects.update_or_create(
        variant=variant,
        defaults={
            "status": "BLOCKED" if blockers else "READY_LOCAL", "previous_values": previous,
            "proposed_metafields": proposed, "evidence_snapshot": evidence, "blockers": blockers,
            "rollback_payload": previous, "idempotency_key": key, "external_writes": 0,
        },
    )
    return preview
