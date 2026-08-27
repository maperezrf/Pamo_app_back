import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import (
    CatalogHistoryEvent,
    ProductVariant,
    SodimacAuditTask,
    SodimacCatalogImportBatch,
    SodimacCatalogImportRow,
    SodimacCatalogLink,
    SodimacCatalogObservation,
    SodimacKit,
    SodimacKitComponent,
    SodimacKitImportBatch,
)
from .physical_measurements import parse_measurement_file


REQUIRED_FIELDS = ("canonical_sku", "sodimac_sku")
OPTIONAL_FIELDS = (
    "listing_id", "listing_url", "barcode", "title", "brand", "description", "image_urls", "attributes",
    "publication_state", "inventory_available", "inventory_source", "inventory_observed_at", "provider",
    "warehouse", "source_date", "last_verified_at",
)
DEFAULT_HEADER_MAPPING = {
    "canonical_sku": "sku_shopify",
    "sodimac_sku": "sku_sodimac",
    "listing_id": "listing_id",
    "listing_url": "url_sodimac",
    "title": "titulo_sodimac",
    "brand": "marca_sodimac",
    "description": "descripcion_sodimac",
    "image_urls": "imagenes_urls",
    "attributes": "atributos_json",
    "publication_state": "estado_publicacion",
    "inventory_available": "inventario",
    "inventory_source": "fuente_inventario",
    "inventory_observed_at": "fecha_inventario",
    "provider": "proveedor",
    "warehouse": "bodega",
    "source_date": "fecha_archivo",
    "last_verified_at": "ultima_verificacion",
}
EVIDENCE_CLASSES = {choice for choice, _ in SodimacCatalogObservation.EvidenceClass.choices}


class SodimacCatalogError(ValueError):
    pass


def normalize_header(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_sku(value):
    return str(value or "").strip().lstrip("'")[:160]


def normalize_identifier(value):
    result = normalize_sku(value)
    if result.endswith(".0") and result[:-2].isdigit():
        return result[:-2]
    return result


def _safe_mapping(mapping):
    supplied = mapping or DEFAULT_HEADER_MAPPING
    cleaned = {key: normalize_header(value) for key, value in supplied.items() if key in REQUIRED_FIELDS + OPTIONAL_FIELDS and value}
    missing = [field for field in REQUIRED_FIELDS if not cleaned.get(field)]
    if missing:
        raise SodimacCatalogError(f"Faltan mapeos requeridos: {', '.join(missing)}")
    return cleaned


def _row_payload(raw, mapping):
    normalized_raw = {normalize_header(key): value for key, value in raw.items()}
    payload = {field: str(normalized_raw.get(header, "") or "").strip() for field, header in mapping.items()}
    payload["canonical_sku"] = normalize_sku(payload.get("canonical_sku"))
    payload["sodimac_sku"] = normalize_sku(payload.get("sodimac_sku"))
    payload["listing_id"] = normalize_sku(payload.get("listing_id"))
    payload["barcode"] = normalize_identifier(payload.get("barcode"))
    payload["image_urls"] = [item.strip() for item in re.split(r"[|;\n]", payload.get("image_urls", "")) if item.strip()]
    attributes = payload.get("attributes", "")
    if attributes:
        try:
            payload["attributes"] = json.loads(attributes)
        except json.JSONDecodeError:
            payload["attributes"] = {"texto": attributes}
    else:
        payload["attributes"] = {}
    inventory = payload.get("inventory_available") or ""
    if inventory != "":
        try:
            payload["inventory_available"] = str(Decimal(inventory.replace(",", ".")))
        except (InvalidOperation, ValueError):
            payload["inventory_available"] = None
            payload.setdefault("row_errors", []).append("INVALID_INVENTORY")
    else:
        payload["inventory_available"] = None
    payload["inventory_source"] = payload.get("inventory_source", "UNKNOWN").upper() or "UNKNOWN"
    if payload["inventory_source"] not in EVIDENCE_CLASSES:
        payload["inventory_source"] = "UNKNOWN"
        payload.setdefault("row_errors", []).append("INVALID_INVENTORY_SOURCE")
    return payload


def _variant_matches(sku):
    return list(ProductVariant.objects.filter(sku__iexact=sku).exclude(
        product__status="STALE_LOCAL_SNAPSHOT",
    ).select_related("product").order_by("id")[:3])


@transaction.atomic
def preview_sodimac_import(
    filename,
    content,
    header_mapping=None,
    actor_label="local-operator",
    is_fixture=False,
    allow_partial=False,
):
    if connection.vendor != "sqlite":
        raise SodimacCatalogError("La importación Sodimac solo puede persistir en SQLite local.")
    mapping = _safe_mapping(header_mapping)
    try:
        raw_rows = parse_measurement_file(filename, content)
    except Exception as error:
        raise SodimacCatalogError(str(error)) from error
    if not raw_rows:
        raise SodimacCatalogError("El archivo no contiene filas para revisar.")
    source_sha256 = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256((source_sha256 + json.dumps(mapping, sort_keys=True)).encode()).hexdigest()
    existing = SodimacCatalogImportBatch.objects.filter(fingerprint=fingerprint).first()
    if existing:
        return existing
    payloads = [_row_payload(raw, mapping) for raw in raw_rows]
    sodimac_owners = {}
    for payload in payloads:
        if payload["sodimac_sku"] and payload["canonical_sku"]:
            sodimac_owners.setdefault(payload["sodimac_sku"].casefold(), set()).add(payload["canonical_sku"].casefold())
    seen = set()
    batch = SodimacCatalogImportBatch.objects.create(
        source_filename=filename[:300], source_sha256=source_sha256, fingerprint=fingerprint,
        source_size_bytes=len(content), source_date=parse_date(payloads[0].get("source_date") or ""),
        header_mapping=mapping, is_fixture=is_fixture, allow_partial=allow_partial,
        actor_label=actor_label[:160], external_writes=0,
    )
    valid = duplicate = conflict = rejected = 0
    for row_number, (raw, payload) in enumerate(zip(raw_rows, payloads), start=2):
        errors = list(payload.pop("row_errors", []))
        conflicts = []
        canonical_sku, sodimac_sku = payload["canonical_sku"], payload["sodimac_sku"]
        if not canonical_sku:
            errors.append("MISSING_CANONICAL_SKU")
        if not sodimac_sku:
            errors.append("MISSING_SODIMAC_SKU")
        variants = _variant_matches(canonical_sku) if canonical_sku else []
        if not variants:
            errors.append("CANONICAL_SKU_NOT_FOUND")
        elif len(variants) > 1:
            conflicts.append("CANONICAL_SKU_AMBIGUOUS")
        if len(sodimac_owners.get(sodimac_sku.casefold(), set())) > 1:
            conflicts.append("SODIMAC_SKU_MAPPED_TO_MULTIPLE_CANONICAL_SKUS")
        identity = (canonical_sku.casefold(), sodimac_sku.casefold(), payload.get("listing_id", "").casefold())
        if identity in seen:
            status = "DUPLICATE"
            duplicate += 1
        elif errors:
            status = "REJECTED"
            rejected += 1
        elif conflicts:
            status = "CONFLICT"
            conflict += 1
        else:
            status = "VALID"
            valid += 1
        seen.add(identity)
        idempotency_key = hashlib.sha256(f"{fingerprint}:{row_number}:{identity}".encode()).hexdigest()
        SodimacCatalogImportRow.objects.create(
            batch=batch, row_number=row_number, canonical_sku=canonical_sku, sodimac_sku=sodimac_sku,
            listing_id=payload.get("listing_id", ""), variant=variants[0] if len(variants) == 1 else None,
            status=status, raw_payload={field: str(value or "")[:2000] for field, value in raw.items()},
            normalized_payload=payload, errors=sorted(set(errors)), conflicts=sorted(set(conflicts)),
            idempotency_key=idempotency_key,
        )
    batch.total_rows = len(payloads)
    batch.valid_rows = valid
    batch.duplicate_rows = duplicate
    batch.conflict_rows = conflict
    batch.rejected_rows = rejected
    if (conflict or rejected) and allow_partial and valid:
        batch.status = "PREVIEW_PARTIAL"
    else:
        batch.status = "BLOCKED" if conflict or rejected else "PREVIEW"
    batch.save()
    return batch


def _tokens(value):
    return {token for token in normalize_header(value).split() if len(token) > 2}


def _similarity(left, right):
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return None
    return round(len(a & b) / len(a | b), 3)


def compare_catalog_content(link, payload):
    product = link.variant.product
    shopify_images = list(product.images.order_by("position").values_list("source_url", flat=True))
    observed_images = payload.get("image_urls") or []
    identity_ok = normalize_sku(link.variant.sku).casefold() == normalize_sku(link.canonical_sku).casefold()
    brand_similarity = _similarity(product.brand or product.vendor, payload.get("brand"))
    title_similarity = _similarity(product.title, payload.get("title"))
    description_similarity = _similarity(product.description_html, payload.get("description"))
    exact_images = [url for url in observed_images if url in shopify_images]
    main_image_exact = bool(observed_images and shopify_images and observed_images[0] == shopify_images[0])
    scores = {
        "identity": 25 if identity_ok else 0,
        "title_brand": round(20 * ((title_similarity or 0) * 0.65 + (brand_similarity or 0) * 0.35)),
        "images": 20 if main_image_exact and len(exact_images) == len(observed_images) == len(shopify_images) else 12 if exact_images else 0,
        "description": round(15 * (description_similarity or 0)),
        "attributes": 10 if payload.get("attributes") else 0,
        "availability": 10 if payload.get("publication_state") and payload.get("inventory_source") != "UNKNOWN" else 5 if payload.get("publication_state") else 0,
    }
    overall = min(sum(scores.values()), 100)
    blockers = []
    if not identity_ok:
        blockers.append("IDENTITY_MISMATCH")
    if not payload.get("listing_url") and not payload.get("listing_id"):
        blockers.append("MISSING_LISTING_ID_OR_URL")
    severity = "BLOCKER" if blockers or overall < 60 else "WARNING" if overall < 85 else "APPROVED"
    comparison = {
        "identity": {"shopify": link.variant.sku, "sodimac": link.canonical_sku, "match": identity_ok, "basis": "CONFIRMED_BY_FILE"},
        "title": {"shopify": product.title, "sodimac": payload.get("title") or None, "similarity": title_similarity, "basis": "OBSERVED_PUBLIC_PAGE" if payload.get("title") else "UNKNOWN"},
        "brand": {"shopify": product.brand or product.vendor, "sodimac": payload.get("brand") or None, "similarity": brand_similarity, "basis": "OBSERVED_PUBLIC_PAGE" if payload.get("brand") else "UNKNOWN"},
        "images": {"shopify_count": len(shopify_images), "sodimac_count": len(observed_images), "exact_url_matches": len(exact_images), "main_exact": main_image_exact, "visual_similarity": "UNKNOWN"},
        "description": {"shopify_present": bool(product.description_html), "sodimac_present": bool(payload.get("description")), "similarity": description_similarity},
        "attributes": {"shopify_count": product.metafields.count(), "sodimac_count": len(payload.get("attributes") or {}), "essential_match": "UNKNOWN"},
        "availability": {"publication_state": payload.get("publication_state") or "UNKNOWN", "inventory": payload.get("inventory_available"), "basis": payload.get("inventory_source") or "UNKNOWN"},
        "blockers": blockers,
        "decision_rule": "APPROVED >=85 sin bloqueos; WARNING 60-84; BLOCKER <60 o identidad/listing faltante.",
    }
    return comparison, scores, overall, severity


@transaction.atomic
def apply_sodimac_import(batch_id, actor_label="local-operator"):
    if connection.vendor != "sqlite":
        raise SodimacCatalogError("La aplicación solo se permite en SQLite local.")
    batch = SodimacCatalogImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status in {"APPLIED_LOCAL", "APPLIED_PARTIAL"}:
        return batch
    allowed_statuses = {"PREVIEW", "PREVIEW_PARTIAL"}
    if batch.status not in allowed_statuses:
        raise SodimacCatalogError("El lote contiene errores o conflictos; no se aplicó ninguna relación.")
    if (batch.conflict_rows or batch.rejected_rows) and not batch.allow_partial:
        raise SodimacCatalogError("El lote contiene errores o conflictos; no se aplicó ninguna relación.")
    created_ids = []
    for row in batch.rows.select_related("variant", "variant__product"):
        if row.status != "VALID" or not row.variant:
            continue
        existing = SodimacCatalogLink.objects.filter(
            canonical_sku=row.canonical_sku, sodimac_sku=row.sodimac_sku, listing_id=row.listing_id,
        ).first()
        if existing:
            if existing.manual_decision:
                row.status = "MANUAL_PRESERVED"
                row.conflicts = sorted(set(row.conflicts + ["MANUAL_DECISION_NOT_OVERWRITTEN"]))
                row.save(update_fields=["status", "conflicts"])
                continue
            row.link = existing
            row.status = "IDEMPOTENT_EXISTING"
            row.save(update_fields=["link", "status"])
            continue
        payload = row.normalized_payload
        last_verified_at = parse_datetime(payload.get("last_verified_at") or "")
        link = SodimacCatalogLink.objects.create(
            variant=row.variant, canonical_sku=row.canonical_sku, sodimac_sku=row.sodimac_sku,
            listing_id=row.listing_id, listing_url=payload.get("listing_url", ""), status="LINKED_EXACT",
            source_checksum=batch.source_sha256, confidence=Decimal("1.0000"), evidence={
                "basis": "CONFIRMED_BY_FILE", "batch_id": str(batch.id), "row_number": row.row_number,
                "fixture": batch.is_fixture, "filename": batch.source_filename,
            }, source_kind="CONFIRMED_BY_FILE", valid_from=batch.source_date or date.today(),
            created_by_batch=batch, last_verified_at=last_verified_at,
        )
        comparison, scores, overall, severity = compare_catalog_content(link, payload)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        observed_at = parse_datetime(payload.get("last_verified_at") or "") or timezone.now()
        observation = SodimacCatalogObservation.objects.create(
            link=link, evidence_class="OBSERVED_PUBLIC_PAGE" if batch.is_fixture else "CONFIRMED_BY_FILE",
            observed_at=observed_at, expires_at=observed_at + timedelta(days=7),
            source_reference=payload.get("listing_url") or f"file:{batch.source_sha256}", source_fingerprint=fingerprint,
            publication_state=payload.get("publication_state") or "UNKNOWN",
            inventory_available=payload.get("inventory_available"), inventory_source=payload.get("inventory_source") or "UNKNOWN",
            raw_payload=payload, field_comparison=comparison, dimension_scores=scores,
            overall_score=overall, severity=severity, external_writes=0,
        )
        task_fingerprint = hashlib.sha256(f"{link.id}:{fingerprint}".encode()).hexdigest()
        SodimacAuditTask.objects.create(
            link=link, status="COMPLETED" if batch.is_fixture else "QUEUED", reason="FIXTURE_LOCAL" if batch.is_fixture else "NEW_FILE_LINK",
            priority=90 if severity == "BLOCKER" else 70 if severity == "WARNING" else 40,
            input_fingerprint=task_fingerprint, cache_key=f"sodimac:{link.sodimac_sku}:{fingerprint[:12]}",
            next_attempt_at=timezone.now(), last_success_observation=observation if batch.is_fixture else None, external_writes=0,
        )
        row.link = link
        row.status = "APPLIED_LOCAL"
        row.save(update_fields=["link", "status"])
        created_ids.append(str(link.id))
    batch.applied_links = len(created_ids)
    batch.rollback_payload = {"created_link_ids": created_ids, "strategy": "DEACTIVATE_AND_MARK_STALE"}
    batch.status = "APPLIED_PARTIAL" if batch.conflict_rows or batch.rejected_rows else "APPLIED_LOCAL"
    batch.save(update_fields=["applied_links", "rollback_payload", "status", "updated_at"])
    CatalogHistoryEvent.objects.create(
        entity_type="SODIMAC_CATALOG_IMPORT", entity_id=str(batch.id), action="APPLY_LOCAL",
        before={}, after={"created_link_ids": created_ids, "fixture": batch.is_fixture},
        reversible=True, actor_label=actor_label[:160],
    )
    return batch


@transaction.atomic
def reverse_sodimac_import(batch_id, actor_label="local-operator"):
    if connection.vendor != "sqlite":
        raise SodimacCatalogError("La reversión solo se permite en SQLite local.")
    batch = SodimacCatalogImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == "REVERSED":
        return batch
    if batch.status not in {"APPLIED_LOCAL", "APPLIED_PARTIAL"}:
        raise SodimacCatalogError("Solo se puede revertir un lote aplicado localmente.")
    link_ids = batch.rollback_payload.get("created_link_ids", [])
    links = SodimacCatalogLink.objects.filter(id__in=link_ids, manual_decision=False, created_by_batch=batch)
    before = list(links.values("id", "status", "active", "valid_until"))
    links.update(active=False, status="STALE", valid_until=date.today())
    batch.status = "REVERSED"
    batch.save(update_fields=["status", "updated_at"])
    CatalogHistoryEvent.objects.create(
        entity_type="SODIMAC_CATALOG_IMPORT", entity_id=str(batch.id), action="REVERSE_LOCAL",
        before={"links": [{**item, "id": str(item["id"]), "valid_until": str(item["valid_until"] or "")} for item in before]},
        after={"active": False, "status": "STALE"}, reversible=True, actor_label=actor_label[:160],
    )
    return batch


@transaction.atomic
def enqueue_incremental_audits(actor_label="local-operator"):
    now = timezone.now()
    created = 0
    for link in SodimacCatalogLink.objects.filter(active=True).select_related("variant", "variant__product"):
        latest = link.observations.first()
        changed = link.variant.product.updated_at > (latest.observed_at if latest else now - timedelta(days=3650))
        expired = not latest or not latest.expires_at or latest.expires_at <= now
        priority = 100 if not latest else 90 if latest.severity == "BLOCKER" else 70 if changed else 50
        if not (expired or changed or priority >= 90):
            continue
        basis = f"{link.id}:{link.variant.product.updated_at.isoformat()}:{latest.source_fingerprint if latest else 'none'}"
        fingerprint = hashlib.sha256(basis.encode()).hexdigest()
        _, was_created = SodimacAuditTask.objects.get_or_create(
            link=link, input_fingerprint=fingerprint,
            defaults={
                "status": "MANUAL_REQUIRED", "reason": "PUBLIC_VERIFICATION_REQUIRES_APPROVED_MECHANISM",
                "priority": priority, "cache_key": f"sodimac:{link.sodimac_sku}:{fingerprint[:12]}",
                "next_attempt_at": now, "last_error_code": "PUBLIC_ADAPTER_DISCONNECTED", "external_writes": 0,
            },
        )
        created += int(was_created)
    CatalogHistoryEvent.objects.create(
        entity_type="SODIMAC_AUDIT_QUEUE", entity_id=now.isoformat(), action="ENQUEUE_INCREMENTAL_LOCAL",
        before={}, after={"created": created, "network_calls": 0}, reversible=False, actor_label=actor_label[:160],
    )
    return created


def serialize_batch(batch, include_rows=False):
    data = {
        "id": str(batch.id), "filename": batch.source_filename, "sha256": batch.source_sha256,
        "fingerprint": batch.fingerprint, "status": batch.status, "is_fixture": batch.is_fixture,
        "allow_partial": batch.allow_partial,
        "total_rows": batch.total_rows, "valid_rows": batch.valid_rows, "duplicate_rows": batch.duplicate_rows,
        "conflict_rows": batch.conflict_rows, "rejected_rows": batch.rejected_rows,
        "applied_links": batch.applied_links, "header_mapping": batch.header_mapping,
        "created_at": batch.created_at, "external_writes": batch.external_writes,
    }
    if include_rows:
        data["rows"] = [{
            "id": row.id, "row_number": row.row_number, "canonical_sku": row.canonical_sku,
            "sodimac_sku": row.sodimac_sku, "listing_id": row.listing_id, "status": row.status,
            "errors": row.errors, "conflicts": row.conflicts,
        } for row in batch.rows.all()]
    return data


def serialize_link(link):
    latest = link.observations.first()
    return {
        "id": str(link.id), "canonical_sku": link.canonical_sku, "sodimac_sku": link.sodimac_sku,
        "listing_id": link.listing_id, "listing_url": link.listing_url, "status": link.status,
        "active": link.active, "source_kind": link.source_kind, "confidence": link.confidence,
        "last_verified_at": link.last_verified_at, "manual_decision": link.manual_decision,
        "evidence": link.evidence,
        "latest_observation": None if not latest else {
            "evidence_class": latest.evidence_class, "observed_at": latest.observed_at,
            "expires_at": latest.expires_at, "publication_state": latest.publication_state,
            "inventory_available": latest.inventory_available, "inventory_source": latest.inventory_source,
            "field_comparison": latest.field_comparison, "dimension_scores": latest.dimension_scores,
            "overall_score": latest.overall_score, "severity": latest.severity,
        },
    }


def build_sodimac_workspace(filters=None):
    from .sodimac_kits import build_sodimac_kit_workspace

    filters = filters or {}
    links = SodimacCatalogLink.objects.filter(active=True).select_related("variant", "variant__product").prefetch_related("observations")
    if filters.get("link_status"):
        links = links.filter(status=filters["link_status"])
    if filters.get("quality"):
        links = links.filter(observations__severity=filters["quality"]).distinct()
    if filters.get("missing") == "yes":
        links = links.filter(Q(observations__isnull=True) | Q(observations__severity="BLOCKER")).distinct()
    if filters.get("missing") == "no":
        links = links.exclude(Q(observations__isnull=True) | Q(observations__severity="BLOCKER")).distinct()
    if filters.get("provider"):
        links = links.filter(variant__product__vendor=filters["provider"])
    if filters.get("warehouse"):
        links = links.filter(observations__raw_payload__warehouse=filters["warehouse"]).distinct()
    if filters.get("inventory") == "known":
        links = links.filter(observations__inventory_available__isnull=False).distinct()
    if filters.get("inventory") == "unknown":
        links = links.filter(Q(observations__isnull=True) | Q(observations__inventory_available__isnull=True)).distinct()
    now = timezone.now()
    if filters.get("freshness") == "current":
        links = links.filter(observations__expires_at__gt=now).distinct()
    if filters.get("freshness") == "stale":
        links = links.filter(observations__expires_at__lte=now).distinct()
    if filters.get("freshness") == "never":
        links = links.filter(observations__isnull=True)
    total_variants = ProductVariant.objects.exclude(sku="").count()
    active_links = SodimacCatalogLink.objects.filter(active=True)
    observations = SodimacCatalogObservation.objects.filter(link__active=True)
    score_averages = observations.aggregate(
        overall=Avg("overall_score"),
    )
    kit_workspace = build_sodimac_kit_workspace()
    return {
        "mode": "LOCAL_ONLY",
        "source_of_identity": "CONFIRMED_BY_FILE",
        "public_page_role": "CHANGEABLE_EVIDENCE_NOT_MASTER",
        "external_writes": 0,
        "real_connectors": {"catalog": "NOT_AVAILABLE", "inventory": "DISCONNECTED", "orders": "DISCONNECTED"},
        "summary": {
            "canonical_variants": total_variants,
            "active_links": active_links.count(),
            "coverage_percent": round(active_links.values("variant_id").distinct().count() * 100 / total_variants, 2) if total_variants else 0,
            "verified": observations.filter(severity="APPROVED", expires_at__gt=now).count(),
            "stale": observations.filter(expires_at__lte=now).count(),
            "critical": observations.filter(severity="BLOCKER").count(),
            "inventory_observable": observations.exclude(inventory_available=None).count(),
            "average_score": round(score_averages["overall"] or 0, 1),
        },
        "links": [serialize_link(link) for link in links[:200]],
        "imports": [serialize_batch(batch, include_rows=True) for batch in SodimacCatalogImportBatch.objects.prefetch_related("rows")[:10]],
        "kit_summary": kit_workspace["summary"],
        "kits": kit_workspace["kits"],
        "kit_imports": kit_workspace["imports"],
        "tasks": [{
            "id": str(task.id), "canonical_sku": task.link.canonical_sku, "sodimac_sku": task.link.sodimac_sku,
            "status": task.status, "reason": task.reason, "priority": task.priority,
            "next_attempt_at": task.next_attempt_at, "attempts": task.attempts,
        } for task in SodimacAuditTask.objects.select_related("link")[:100]],
        "scoring": {
            "weights": {"identity": 25, "title_brand": 20, "images": 20, "description": 15, "attributes": 10, "availability": 10},
            "approval": ">=85 sin bloqueos", "warning": "60-84", "blocker": "<60 o identidad/listing faltante",
        },
        "daily_contract": {
            "scheduler": "NOT_CONFIGURED", "strategy": "risk_based_incremental",
            "eligible": ["expired", "source_changed", "blocker", "priority"],
            "cache": "fingerprint_per_listing", "backoff": "exponential_3_attempts",
            "rate_limit": "provider_contract_required", "captcha_bypass": False,
            "fallback": "manual_or_file", "server_ready": True,
        },
        "adapters": {
            "catalog_file": {"status": "IMPLEMENTED_LOCAL", "writes": 0},
            "public_page": {"status": "CONTRACT_ONLY", "requires": "legal_and_technical_approval"},
            "inventory_api": {"status": "DISCONNECTED", "evidence_scope": "inventory_only"},
            "orders_api": {"status": "DISCONNECTED", "evidence_scope": "orders_only_not_catalog_quality"},
        },
        "required_columns": list(REQUIRED_FIELDS),
        "optional_columns": list(OPTIONAL_FIELDS),
        "default_header_mapping": DEFAULT_HEADER_MAPPING,
    }
