import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import (
    CatalogHistoryEvent,
    PhysicalEnrichmentPilotSelection,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    PhysicalMeasurementImportBatch,
    PhysicalMeasurementImportRow,
    PhysicalMeasurementTask,
    ProviderConfig,
    ShopifyPhysicalUpdatePreview,
    SkuReconciliation,
)
from .physical import PhysicalValidationError, build_shopify_preview, normalize_measurement, upsert_candidate


ALLOWED_SOURCES = {"PROVEEDOR_EXACTO", "MEDICION_FISICA", "DEMO_NO_CONFIRMADO"}
FIELDS = (("weight", "WEIGHT"), ("length", "LENGTH"), ("width", "WIDTH"), ("height", "HEIGHT"))


class PhysicalMeasurementImportError(ValueError):
    pass


def normalize_header(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


HEADER_MAP = {
    "sku": "sku", "gtin": "gtin", "descripcion": "description", "proveedor": "provider",
    "peso empacado": "weight", "unidad peso": "weight_unit", "largo paquete": "length",
    "ancho paquete": "width", "alto paquete": "height", "unidad dimensiones": "dimension_unit",
    "cantidad bultos": "package_count", "fecha verificacion": "verified_date", "responsable": "responsible",
    "tipo de fuente": "source_kind", "fuente referencia": "source_reference",
    "evidencia foto url": "evidence_url", "observaciones": "notes",
}


def clean_identifier(value, prefix):
    text = str(value or "").strip().lstrip("'")
    return re.sub(rf"^{prefix}\s*:\s*", "", text, flags=re.I).strip()


def parse_csv_rows(content):
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{HEADER_MAP.get(normalize_header(key), normalize_header(key)): value for key, value in row.items()} for row in reader]


def _xlsx_cell_value(cell, shared_strings):
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell_type = cell.attrib.get("t")
    inline = cell.find("x:is", namespace)
    if inline is not None:
        return "".join(node.text or "" for node in inline.findall(".//x:t", namespace))
    node = cell.find("x:v", namespace)
    if node is None:
        return ""
    raw = node.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    if cell_type in {"str", "inlineStr"}:
        return raw
    return raw


def parse_xlsx_rows(content):
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise PhysicalMeasurementImportError("El archivo XLSX no es válido.") from error
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared_strings = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root.findall("x:si", namespace):
            shared_strings.append("".join(node.text or "" for node in item.findall(".//x:t", namespace)))
    sheet_paths = sorted(name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
    if not sheet_paths:
        raise PhysicalMeasurementImportError("El XLSX no contiene hojas legibles.")
    root = ElementTree.fromstring(archive.read(sheet_paths[0]))
    matrix = []
    for row in root.findall(".//x:sheetData/x:row", namespace):
        values = {}
        for cell in row.findall("x:c", namespace):
            reference = cell.attrib.get("r", "")
            letters = re.match(r"[A-Z]+", reference)
            if not letters:
                continue
            column = 0
            for char in letters.group(0):
                column = column * 26 + ord(char) - 64
            values[column - 1] = _xlsx_cell_value(cell, shared_strings)
        if values:
            matrix.append([values.get(index, "") for index in range(max(values) + 1)])
    if not matrix:
        return []
    headers = [HEADER_MAP.get(normalize_header(value), normalize_header(value)) for value in matrix[0]]
    return [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in matrix[1:] if any(str(value).strip() for value in row)]


def parse_measurement_file(filename, content):
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        return parse_csv_rows(content)
    if suffix == "xlsx":
        return parse_xlsx_rows(content)
    raise PhysicalMeasurementImportError("Formato no permitido. Use XLSX o CSV.")


def _decimal(value, label, errors):
    if value in (None, ""):
        errors.append(f"MISSING_{label.upper()}")
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        errors.append(f"INVALID_{label.upper()}")
        return None


def _verified_date(value, errors):
    if value in (None, ""):
        errors.append("MISSING_VERIFIED_DATE")
        return None
    parsed = parse_date(str(value).strip())
    if parsed:
        return parsed
    try:
        serial = int(Decimal(str(value)))
        return date(1899, 12, 30) + timedelta(days=serial)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        errors.append("INVALID_VERIFIED_DATE")
        return None


def validate_measurement_row(raw, provider):
    errors, conflicts = [], []
    sku = clean_identifier(raw.get("sku"), "SKU")
    gtin = clean_identifier(raw.get("gtin"), "GTIN")
    if not sku:
        errors.append("MISSING_SKU")
    reconciliations = list(SkuReconciliation.objects.filter(
        supplier_item__provider=provider, supplier_item__supplier_sku=sku, status="EXACT", variant__isnull=False,
    ).select_related("supplier_item", "variant")[:2])
    if len(reconciliations) != 1:
        errors.append("SKU_NOT_UNIQUE_EXACT_MATCH")
        reconciliation = None
    else:
        reconciliation = reconciliations[0]
        if gtin and reconciliation.variant.barcode and gtin != reconciliation.variant.barcode:
            errors.append("GTIN_MISMATCH")
    if str(raw.get("provider") or "").strip().casefold() != provider.name.casefold():
        errors.append("PROVIDER_MISMATCH")
    source_kind = str(raw.get("source_kind") or "").strip().upper()
    if source_kind not in ALLOWED_SOURCES:
        errors.append("INVALID_SOURCE_KIND")
    responsible = str(raw.get("responsible") or "").strip()
    source_reference = str(raw.get("source_reference") or "").strip()
    if not responsible:
        errors.append("MISSING_RESPONSIBLE")
    if not source_reference:
        errors.append("MISSING_SOURCE_REFERENCE")
    verified_date = _verified_date(raw.get("verified_date"), errors)
    try:
        package_count = int(Decimal(str(raw.get("package_count") or "0")))
    except (InvalidOperation, ValueError, TypeError):
        package_count = 0
    if package_count <= 0 or package_count > 20:
        errors.append("INVALID_PACKAGE_COUNT")
    elif package_count > 1:
        conflicts.append("MULTIPACK_REQUIRES_PER_PACKAGE_DETAIL")
    values = {
        "weight": _decimal(raw.get("weight"), "weight", errors),
        "length": _decimal(raw.get("length"), "length", errors),
        "width": _decimal(raw.get("width"), "width", errors),
        "height": _decimal(raw.get("height"), "height", errors),
    }
    units = {
        "weight": str(raw.get("weight_unit") or "").strip().upper(),
        "length": str(raw.get("dimension_unit") or "").strip().upper(),
        "width": str(raw.get("dimension_unit") or "").strip().upper(),
        "height": str(raw.get("dimension_unit") or "").strip().upper(),
    }
    normalized = {}
    for key, field in FIELDS:
        if values[key] is None:
            continue
        try:
            result, unit = normalize_measurement(field, values[key], units[key])
            normalized[key] = {"value": str(result), "unit": unit, "original_value": str(values[key]), "original_unit": units[key]}
        except PhysicalValidationError as error:
            errors.append(f"INVALID_{key.upper()}:{error}")
    if reconciliation:
        for key, field in FIELDS:
            candidate = normalized.get(key)
            if not candidate:
                continue
            existing = PhysicalEvidenceCandidate.objects.filter(
                variant=reconciliation.variant, field=field, scope="PACKAGE", classification="CONFIRMED",
            ).order_by("-observed_at").first()
            if existing:
                new_value = Decimal(candidate["value"])
                larger = max(existing.normalized_value, new_value)
                if larger and abs(existing.normalized_value - new_value) / larger > Decimal("0.20"):
                    conflicts.append(f"{field}_DIFFERS_OVER_20_PERCENT_FROM_{existing.id}")
    return {
        "sku": sku, "gtin": gtin, "reconciliation": reconciliation, "source_kind": source_kind,
        "responsible": responsible, "source_reference": source_reference,
        "evidence_url": str(raw.get("evidence_url") or "").strip(), "notes": str(raw.get("notes") or "").strip(),
        "verified_date": verified_date.isoformat() if verified_date else None, "package_count": package_count,
        "measurements": normalized, "errors": sorted(set(errors)), "conflicts": sorted(set(conflicts)),
        "is_demo": source_kind == "DEMO_NO_CONFIRMADO",
    }


@transaction.atomic
def preview_measurement_import(provider_name, filename, content):
    if connection.vendor != "sqlite":
        raise PhysicalMeasurementImportError("La captura física solo puede persistir en SQLite local.")
    provider = ProviderConfig.objects.get(name=provider_name)
    rows = parse_measurement_file(filename, content)
    if not rows:
        raise PhysicalMeasurementImportError("El archivo no contiene filas para revisar.")
    source_sha256 = hashlib.sha256(content).hexdigest()
    validations = [validate_measurement_row(raw, provider) for raw in rows]
    is_demo = bool(validations) and all(row["is_demo"] for row in validations)
    batch, created = PhysicalMeasurementImportBatch.objects.get_or_create(
        provider=provider, source_sha256=source_sha256,
        defaults={"source_filename": filename, "is_demo": is_demo, "status": "PREVIEW", "external_writes": 0},
    )
    if not created and batch.status in {"IMPORTED_LOCAL", "REVERSED"}:
        return batch
    valid_rows = error_rows = conflict_rows = 0
    for index, (raw, validation) in enumerate(zip(rows, validations), start=2):
        status = "ERROR" if validation["errors"] else "CONFLICT" if validation["conflicts"] else "DEMO_BLOCKED" if validation["is_demo"] else "VALID"
        valid_rows += status in {"VALID", "DEMO_BLOCKED"}
        error_rows += status == "ERROR"
        conflict_rows += status == "CONFLICT"
        fingerprint = hashlib.sha256(f"{source_sha256}:{index}:{validation['sku']}".encode()).hexdigest()
        reconciliation = validation.pop("reconciliation")
        PhysicalMeasurementImportRow.objects.update_or_create(
            idempotency_key=fingerprint,
            defaults={
                "batch": batch, "supplier_item": reconciliation.supplier_item if reconciliation else None,
                "variant": reconciliation.variant if reconciliation else None, "row_number": index,
                "sku": validation["sku"], "status": status, "raw_payload": raw,
                "normalized_payload": validation, "errors": validation["errors"], "conflicts": validation["conflicts"],
            },
        )
    batch.source_filename = filename
    batch.is_demo = is_demo
    batch.total_rows = len(rows)
    batch.valid_rows = valid_rows
    batch.error_rows = error_rows
    batch.conflict_rows = conflict_rows
    batch.status = "DEMO_VALIDATED" if is_demo and error_rows == 0 and conflict_rows == 0 else "PREVIEW"
    batch.save()
    return batch


def serialize_import_batch(batch):
    return {
        "id": batch.id, "filename": batch.source_filename, "status": batch.status, "is_demo": batch.is_demo,
        "total_rows": batch.total_rows, "valid_rows": batch.valid_rows, "error_rows": batch.error_rows,
        "conflict_rows": batch.conflict_rows, "external_writes": batch.external_writes,
        "rows": [{
            "id": row.id, "row_number": row.row_number, "sku": row.sku, "status": row.status,
            "normalized": row.normalized_payload, "errors": row.errors, "conflicts": row.conflicts,
            "candidate_ids": row.candidate_ids,
        } for row in batch.rows.all()],
        "demo_gate": "DEMO_NEVER_CREATES_CONFIRMED_EVIDENCE" if batch.is_demo else None,
    }


@transaction.atomic
def apply_measurement_import(batch_id, actor_label="local-operator"):
    batch = PhysicalMeasurementImportBatch.objects.select_for_update().get(pk=batch_id)
    if connection.vendor != "sqlite":
        raise PhysicalMeasurementImportError("La importación solo se permite en SQLite local.")
    if batch.is_demo:
        batch.status = "DEMO_VALIDATED"
        batch.save(update_fields=["status", "updated_at"])
        return batch
    if batch.error_rows or batch.conflict_rows:
        raise PhysicalMeasurementImportError("El lote contiene errores o conflictos y no puede aplicarse.")
    if batch.status == "IMPORTED_LOCAL":
        return batch
    before = {}
    for row in batch.rows.select_related("variant", "supplier_item"):
        if row.status != "VALID":
            continue
        payload = row.normalized_payload
        verified = datetime.combine(date.fromisoformat(payload["verified_date"]), time.min)
        observed_at = timezone.make_aware(verified, timezone.get_current_timezone())
        candidate_ids = []
        before[row.sku] = [{
            "id": candidate.id, "field": candidate.field, "normalized_value": str(candidate.normalized_value),
            "normalized_unit": candidate.normalized_unit, "classification": candidate.classification,
            "conflict": candidate.conflict,
        } for candidate in PhysicalEvidenceCandidate.objects.filter(variant=row.variant, scope="PACKAGE")]
        for key, field in FIELDS:
            measurement = payload["measurements"][key]
            source_type = "PROVIDER_CATALOG" if payload["source_kind"] == "PROVEEDOR_EXACTO" else "MANUAL"
            candidate = upsert_candidate(
                variant=row.variant, supplier_item=row.supplier_item, field=field, scope="PACKAGE", classification="CONFIRMED",
                source_type=source_type, source_reference=payload["source_reference"], source_url=payload["evidence_url"],
                evidence_excerpt=(f"{payload['source_kind']} · SKU {row.sku} · verificado {payload['verified_date']} "
                                  f"por {payload['responsible']} · bultos {payload['package_count']} · {payload['notes']}")[:700],
                original_value=measurement["original_value"], original_unit=measurement["original_unit"],
                confidence=Decimal("0.9900") if source_type == "MANUAL" else Decimal("0.9700"),
                identifier_type="SKU", identifier_value=row.sku, selector=f"measurement-import:{batch.id}:row:{row.row_number}:{field}",
                extraction_method="PHYSICAL_MEASUREMENT_V1" if source_type == "MANUAL" else "PROVIDER_EXACT_PACKAGE_V1",
                observed_at=observed_at,
            )
            candidate_ids.append(candidate.id)
        row.status = "IMPORTED_CONFIRMED"
        row.candidate_ids = candidate_ids
        row.save(update_fields=["status", "candidate_ids", "updated_at"])
        build_shopify_preview(row.variant)
        PhysicalMeasurementTask.objects.filter(variant=row.variant, action__in=["REGISTER_MEASUREMENT", "IMPORT_FILE"]).update(status="COMPLETED")
    batch.snapshot_before = before
    batch.rollback_snapshot = {"strategy": "REJECT_IMPORTED_CANDIDATES", "candidate_ids": [candidate_id for row in batch.rows.all() for candidate_id in row.candidate_ids]}
    batch.status = "IMPORTED_LOCAL"
    batch.save()
    CatalogHistoryEvent.objects.create(
        entity_type="PHYSICAL_MEASUREMENT_IMPORT", entity_id=str(batch.id), action="IMPORT_CONFIRMED_PACKAGE_LOCAL",
        before=before, after=serialize_import_batch(batch), reversible=True, actor_label=actor_label,
    )
    return batch


@transaction.atomic
def reverse_measurement_import(batch_id, actor_label="local-operator"):
    batch = PhysicalMeasurementImportBatch.objects.select_for_update().get(pk=batch_id)
    if connection.vendor != "sqlite":
        raise PhysicalMeasurementImportError("La reversión solo se permite en SQLite local.")
    if batch.status == "REVERSED":
        return batch
    if batch.status != "IMPORTED_LOCAL":
        raise PhysicalMeasurementImportError("Solo un lote importado localmente puede revertirse.")
    before = serialize_import_batch(batch)
    variants = set()
    for row in batch.rows.select_related("variant"):
        for candidate in PhysicalEvidenceCandidate.objects.filter(id__in=row.candidate_ids):
            PhysicalEvidenceDecision.objects.create(
                candidate=candidate, action="REJECT", reason=f"Reversión local del lote {batch.id}",
                actor_label=actor_label, decision_snapshot={"batch_id": batch.id, "candidate_fingerprint": candidate.content_fingerprint},
                external_writes=0,
            )
        if row.variant:
            variants.add(row.variant)
        row.status = "REVERSED"
        row.save(update_fields=["status", "updated_at"])
    batch.status = "REVERSED"
    batch.save(update_fields=["status", "updated_at"])
    for variant in variants:
        build_shopify_preview(variant)
    CatalogHistoryEvent.objects.create(
        entity_type="PHYSICAL_MEASUREMENT_IMPORT", entity_id=str(batch.id), action="REVERSE_PACKAGE_IMPORT_LOCAL",
        before=before, after=serialize_import_batch(batch), reversible=False, actor_label=actor_label,
    )
    return batch


@transaction.atomic
def create_measurement_task(variant_id, action, actor_label="local-operator", note=""):
    if action not in PhysicalMeasurementTask.Action.values:
        raise PhysicalMeasurementImportError("Acción de captura no permitida.")
    selection = PhysicalEnrichmentPilotSelection.objects.select_related("supplier_item", "variant").get(variant_id=variant_id)
    approved = set()
    for field in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT"):
        candidate = PhysicalEvidenceCandidate.objects.filter(variant=selection.variant, field=field, scope="PACKAGE", classification="CONFIRMED").prefetch_related("decisions").first()
        if candidate and candidate.decisions.first() and candidate.decisions.first().action == "APPROVE_LOCAL" and not candidate.conflict:
            approved.add(field)
    missing = [field for field in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT") if field not in approved]
    task, _ = PhysicalMeasurementTask.objects.update_or_create(
        variant=selection.variant, action=action,
        defaults={"supplier_item": selection.supplier_item, "status": "OPEN", "missing_fields": missing,
                  "note": str(note or "")[:500], "actor_label": actor_label, "external_writes": 0},
    )
    CatalogHistoryEvent.objects.create(
        entity_type="PHYSICAL_MEASUREMENT_TASK", entity_id=str(task.id), action=action,
        before={}, after={"sku": selection.variant.sku, "missing_fields": missing, "status": task.status},
        reversible=True, actor_label=actor_label,
    )
    return task


def measurement_workspace(provider_name="Barú"):
    selections = PhysicalEnrichmentPilotSelection.objects.select_related("variant__product", "supplier_item").all()
    progress = []
    for selection in selections:
        confirmed = set(PhysicalEvidenceCandidate.objects.filter(
            variant=selection.variant, scope="PACKAGE", classification="CONFIRMED", conflict=False,
        ).values_list("field", flat=True))
        approved = set()
        for candidate in PhysicalEvidenceCandidate.objects.filter(
            variant=selection.variant, scope="PACKAGE", classification="CONFIRMED", conflict=False,
        ).prefetch_related("decisions"):
            decision = candidate.decisions.first()
            if decision and decision.action == "APPROVE_LOCAL":
                approved.add(candidate.field)
        missing = [field for field in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT") if field not in approved]
        progress.append({
            "variant_id": selection.variant_id, "sku": selection.variant.sku, "gtin": selection.variant.barcode,
            "title": selection.variant.product.title,
            "rank": selection.rank, "confirmed_fields": sorted(confirmed), "approved_fields": sorted(approved),
            "missing_fields": missing, "progress_percent": int(len(approved) / 4 * 100),
            "tasks": [{"id": task.id, "action": task.action, "status": task.status, "note": task.note} for task in selection.variant.physical_tasks.all()],
        })
    batches = PhysicalMeasurementImportBatch.objects.filter(provider__name=provider_name).prefetch_related("rows")[:10]
    return {
        "provider": provider_name, "pilot_size": len(progress), "complete_package_sets": sum(not row["missing_fields"] for row in progress),
        "progress": progress, "imports": [serialize_import_batch(batch) for batch in batches],
        "template_url": "/api/catalogo/physical/measurement-template/",
        "rules": {
            "confirmed_sources": ["PROVEEDOR_EXACTO", "MEDICION_FISICA"],
            "demo": "DEMO_NO_CONFIRMADO nunca crea evidencia confirmada ni elegibilidad.",
            "multipack": "Más de un bulto exige detalle por paquete y queda bloqueado.",
        },
        "external_writes": 0,
    }
