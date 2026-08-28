import hashlib
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction

from .models import (
    CatalogHistoryEvent,
    ProductVariant,
    SodimacKit,
    SodimacKitComponent,
    SodimacKitImportBatch,
)
from .physical_measurements import parse_measurement_file
from .sodimac_catalog import normalize_identifier, normalize_sku


REQUIRED_KIT_COLUMNS = ("kitnumber", "sku", "quantity")


class SodimacKitError(ValueError):
    pass


def _variant_matches(sku):
    return list(ProductVariant.objects.filter(sku__iexact=sku).exclude(
        product__status="STALE_LOCAL_SNAPSHOT",
    ).order_by("id")[:3])


def _positive_integer(value, row_number):
    try:
        quantity = Decimal(str(value or "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise SodimacKitError(f"Cantidad inválida en la fila {row_number}.") from error
    if quantity <= 0 or quantity != quantity.to_integral_value():
        raise SodimacKitError(f"La cantidad de la fila {row_number} debe ser un entero positivo.")
    return int(quantity)


def _normalize_rows(raw_rows):
    normalized = []
    for row_number, raw in enumerate(raw_rows, start=2):
        missing_headers = [column for column in REQUIRED_KIT_COLUMNS if column not in raw]
        if missing_headers:
            raise SodimacKitError(f"Faltan columnas requeridas: {', '.join(missing_headers)}.")
        kit_sku = normalize_identifier(raw.get("kitnumber"))
        component_sku = normalize_sku(raw.get("sku"))
        if not kit_sku or not component_sku:
            raise SodimacKitError(f"La fila {row_number} no identifica kit y componente.")
        normalized.append({
            "row_number": row_number,
            "kit_sku": kit_sku,
            "ean": normalize_identifier(raw.get("ean")),
            "component_sku": component_sku,
            "quantity": _positive_integer(raw.get("quantity"), row_number),
        })
    if not normalized:
        raise SodimacKitError("El archivo de kits no contiene componentes.")
    return normalized


@transaction.atomic
def import_sodimac_kits(filename, content, canonical_by_sodimac=None, actor_label="local-operator"):
    if connection.vendor != "sqlite":
        raise SodimacKitError("Los kits Sodimac solo pueden persistir en SQLite local.")
    try:
        raw_rows = parse_measurement_file(filename, content)
    except Exception as error:
        raise SodimacKitError(str(error)) from error
    rows = _normalize_rows(raw_rows)
    source_sha256 = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(f"sodimac-kit-recipe-v1:{source_sha256}".encode()).hexdigest()
    existing = SodimacKitImportBatch.objects.filter(fingerprint=fingerprint).first()
    if existing:
        return existing

    canonical_by_sodimac = {
        normalize_identifier(key): normalize_sku(value)
        for key, value in (canonical_by_sodimac or {}).items()
        if key
    }
    grouped = defaultdict(lambda: {"ean": set(), "components": {}})
    for row in rows:
        group = grouped[row["kit_sku"]]
        if row["ean"]:
            group["ean"].add(row["ean"])
        component = group["components"].setdefault(row["component_sku"].casefold(), {
            "sku": row["component_sku"], "quantity": 0, "row_number": row["row_number"],
        })
        component["quantity"] += row["quantity"]
        component["row_number"] = min(component["row_number"], row["row_number"])
    multiple_ean = [kit_sku for kit_sku, group in grouped.items() if len(group["ean"]) > 1]
    if multiple_ean:
        raise SodimacKitError(f"Hay kits con más de un EAN: {', '.join(sorted(multiple_ean)[:10])}.")

    previous_active_ids = list(SodimacKit.objects.filter(active=True).values_list("id", flat=True))
    batch = SodimacKitImportBatch.objects.create(
        source_filename=filename[:300], source_sha256=source_sha256, fingerprint=fingerprint,
        source_size_bytes=len(content), actor_label=actor_label[:160], external_writes=0,
    )
    created_ids = []
    exact_components = missing_components = ambiguous_components = resolved_kits = review_kits = 0
    for kit_sku, group in sorted(grouped.items()):
        canonical_sku = canonical_by_sodimac.get(kit_sku, "")
        canonical_matches = _variant_matches(canonical_sku) if canonical_sku else []
        component_rows = []
        for component in sorted(group["components"].values(), key=lambda item: item["row_number"]):
            matches = _variant_matches(component["sku"])
            if len(matches) == 1:
                match_status = SodimacKitComponent.MatchStatus.EXACT_SKU
                variant = matches[0]
                candidates = []
                exact_components += 1
            elif len(matches) > 1:
                match_status = SodimacKitComponent.MatchStatus.AMBIGUOUS_SKU
                variant = None
                candidates = [str(item.id) for item in matches]
                ambiguous_components += 1
            else:
                match_status = SodimacKitComponent.MatchStatus.MISSING_SHOPIFY
                variant = None
                candidates = []
                missing_components += 1
            component_rows.append({
                **component,
                "match_status": match_status,
                "variant": variant,
                "candidates": candidates,
            })
        status = (
            SodimacKit.Status.RESOLVED
            if all(item["match_status"] == SodimacKitComponent.MatchStatus.EXACT_SKU for item in component_rows)
            else SodimacKit.Status.PARTIAL
        )
        resolved_kits += int(status == SodimacKit.Status.RESOLVED)
        review_kits += int(status == SodimacKit.Status.PARTIAL)
        kit = SodimacKit.objects.create(
            sodimac_kit_sku=kit_sku,
            canonical_sku=canonical_sku,
            canonical_variant=canonical_matches[0] if len(canonical_matches) == 1 else None,
            ean=next(iter(group["ean"]), ""),
            status=status,
            source_checksum=source_sha256,
            created_by_batch=batch,
        )
        created_ids.append(str(kit.id))
        SodimacKitComponent.objects.bulk_create([
            SodimacKitComponent(
                kit=kit,
                row_number=item["row_number"],
                component_sku=item["sku"],
                component_variant=item["variant"],
                quantity=item["quantity"],
                match_status=item["match_status"],
                candidate_variant_ids=item["candidates"],
            )
            for item in component_rows
        ])

    if previous_active_ids:
        SodimacKit.objects.filter(id__in=previous_active_ids).update(active=False)
    batch.kit_count = len(grouped)
    batch.component_rows = len(rows)
    batch.resolved_kits = resolved_kits
    batch.review_kits = review_kits
    batch.exact_components = exact_components
    batch.missing_components = missing_components
    batch.ambiguous_components = ambiguous_components
    batch.rollback_payload = {
        "created_kit_ids": created_ids,
        "previous_active_ids": [str(item) for item in previous_active_ids],
        "strategy": "DEACTIVATE_CURRENT_AND_REACTIVATE_PREVIOUS",
    }
    batch.save()
    CatalogHistoryEvent.objects.create(
        entity_type="SODIMAC_KIT_IMPORT",
        entity_id=str(batch.id),
        action="APPLY_LOCAL",
        before={"active_kit_ids": [str(item) for item in previous_active_ids]},
        after={
            "kit_count": batch.kit_count,
            "component_rows": batch.component_rows,
            "resolved_kits": batch.resolved_kits,
            "review_kits": batch.review_kits,
            "external_writes": 0,
        },
        reversible=True,
        actor_label=actor_label[:160],
    )
    return batch


@transaction.atomic
def reverse_sodimac_kit_import(batch_id, actor_label="local-operator"):
    batch = SodimacKitImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == SodimacKitImportBatch.Status.REVERSED:
        return batch
    created_ids = batch.rollback_payload.get("created_kit_ids", [])
    previous_ids = batch.rollback_payload.get("previous_active_ids", [])
    SodimacKit.objects.filter(id__in=created_ids).update(active=False)
    SodimacKit.objects.filter(id__in=previous_ids).update(active=True)
    batch.status = SodimacKitImportBatch.Status.REVERSED
    batch.save(update_fields=["status", "updated_at"])
    CatalogHistoryEvent.objects.create(
        entity_type="SODIMAC_KIT_IMPORT", entity_id=str(batch.id), action="REVERSE_LOCAL",
        before={"created_active": created_ids}, after={"reactivated": previous_ids},
        reversible=True, actor_label=actor_label[:160],
    )
    return batch


def _canonical_inventory(variant):
    snapshots = [item for item in variant.inventory_sources.all() if item.canonical]
    if len(snapshots) != 1 or snapshots[0].stock_unknown or snapshots[0].reported_stock is None:
        return None
    snapshot = snapshots[0]
    return max(Decimal("0"), snapshot.reported_stock - snapshot.reserved_stock - snapshot.safety_stock)


def serialize_kit(kit):
    component_data = []
    total_cost = Decimal("0")
    total_reference_price = Decimal("0")
    cost_complete = price_complete = True
    possible_units = []
    for component in kit.components.all():
        variant = component.component_variant
        unit_cost = None
        unit_price = None
        available = None
        if variant:
            try:
                unit_cost = variant.canonical_cost.observation.raw_cost
            except (AttributeError, ObjectDoesNotExist):
                unit_cost = None
            unit_price = variant.price
            available = _canonical_inventory(variant)
        extended_cost = unit_cost * component.quantity if unit_cost is not None else None
        extended_price = unit_price * component.quantity if unit_price is not None else None
        if extended_cost is None:
            cost_complete = False
        else:
            total_cost += extended_cost
        if extended_price is None:
            price_complete = False
        else:
            total_reference_price += extended_price
        if available is None:
            possible_units = None
        elif possible_units is not None:
            possible_units.append(int(available // component.quantity))
        component_data.append({
            "sku": component.component_sku,
            "quantity": component.quantity,
            "match_status": component.match_status,
            "matched_sku": variant.sku if variant else None,
            "unit_cost": unit_cost,
            "extended_cost": extended_cost,
            "unit_reference_price": unit_price,
            "extended_reference_price": extended_price,
            "available_to_promise": available,
        })
    all_components_exact = all(
        item["match_status"] == SodimacKitComponent.MatchStatus.EXACT_SKU for item in component_data
    )
    cost_complete = cost_complete and all_components_exact
    price_complete = price_complete and all_components_exact
    return {
        "id": str(kit.id),
        "sodimac_kit_sku": kit.sodimac_kit_sku,
        "canonical_sku": kit.canonical_sku,
        "canonical_match": "EXACT_SKU" if kit.canonical_variant else "NOT_LINKED",
        "ean": kit.ean,
        "status": kit.status,
        "component_count": len(component_data),
        "components": component_data,
        "economics": {
            "cost_complete": cost_complete,
            "component_cost_total": total_cost if cost_complete else None,
            "reference_price_complete": price_complete,
            "component_reference_price_total": total_reference_price if price_complete else None,
            "recommended_sale_price": None,
            "pricing_status": "READY_FOR_APPROVED_SODIMAC_POLICY" if cost_complete else "BLOCKED_INCOMPLETE_COMPONENT_COST",
            "possible_kit_units": min(possible_units) if possible_units else None,
            "inventory_status": "AVAILABLE" if possible_units else "BLOCKED_UNKNOWN_COMPONENT_INVENTORY",
        },
    }


def build_sodimac_kit_workspace():
    kits = SodimacKit.objects.filter(active=True).select_related(
        "canonical_variant",
    ).prefetch_related(
        "components__component_variant__canonical_cost__observation",
        "components__component_variant__inventory_sources",
    )
    serialized = [serialize_kit(kit) for kit in kits]
    return {
        "summary": {
            "kits": len(serialized),
            "resolved": sum(item["status"] == SodimacKit.Status.RESOLVED for item in serialized),
            "review": sum(item["status"] == SodimacKit.Status.PARTIAL for item in serialized),
            "component_rows": sum(item["component_count"] for item in serialized),
            "cost_complete": sum(item["economics"]["cost_complete"] for item in serialized),
            "inventory_complete": sum(item["economics"]["possible_kit_units"] is not None for item in serialized),
        },
        "kits": serialized,
        "imports": [{
            "id": str(batch.id),
            "filename": batch.source_filename,
            "status": batch.status,
            "kit_count": batch.kit_count,
            "component_rows": batch.component_rows,
            "resolved_kits": batch.resolved_kits,
            "review_kits": batch.review_kits,
            "exact_components": batch.exact_components,
            "missing_components": batch.missing_components,
            "ambiguous_components": batch.ambiguous_components,
            "created_at": batch.created_at,
            "external_writes": batch.external_writes,
        } for batch in SodimacKitImportBatch.objects.all()[:10]],
    }
