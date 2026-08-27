from collections import Counter
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    CatalogHistoryEvent,
    Channel,
    ChannelSnapshot,
    ExternalChannelProductSnapshot,
    IntegrationReadStatus,
    ProductVariant,
)


SUPPORTED_EXTERNAL_CHANNELS = {Channel.MERCADO_LIBRE, Channel.FALABELLA, Channel.MADECENTRO}


class ChannelImportError(ValueError):
    pass


def _text(value, limit=None):
    result = str(value or "").strip()
    return result[:limit] if limit else result


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ChannelImportError("El snapshot contiene un precio o inventario no numérico.") from error


def _datetime(value, fallback):
    if hasattr(value, "tzinfo"):
        return value
    return parse_datetime(_text(value)) or fallback


def _match_record(record):
    sku = _text(record.get("sku"), 160)
    barcode = _text(record.get("barcode"), 160)
    if sku:
        matches = list(ProductVariant.objects.filter(sku__iexact=sku).exclude(product__status="STALE_LOCAL_SNAPSHOT").select_related("product")[:3])
        if len(matches) == 1:
            return ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU, matches[0], [], "SKU exacto y único en Shopify."
        if len(matches) > 1:
            return ExternalChannelProductSnapshot.MatchStatus.AMBIGUOUS_SKU, None, [str(row.id) for row in matches], "El SKU existe en más de una variante Shopify."
        return ExternalChannelProductSnapshot.MatchStatus.MISSING_SHOPIFY, None, [], "El SKU del canal no existe en el snapshot Shopify."
    if barcode:
        candidates = list(ProductVariant.objects.filter(barcode=barcode).exclude(product__status="STALE_LOCAL_SNAPSHOT").select_related("product")[:3])
        if candidates:
            return ExternalChannelProductSnapshot.MatchStatus.IDENTIFIER_REVIEW, None, [str(row.id) for row in candidates], "El código de barras propone candidatos, pero no autoriza vínculo automático."
    return ExternalChannelProductSnapshot.MatchStatus.MISSING_SKU, None, [], "El canal no entregó un SKU verificable."


def _normal(record, observed_at):
    external_product_id = _text(record.get("external_product_id") or record.get("id"), 160)
    if not external_product_id:
        raise ChannelImportError("Cada registro debe incluir external_product_id.")
    images = record.get("images") if isinstance(record.get("images"), list) else []
    image_url = _text(record.get("image_url") or (images[0] if images else ""), 2048)
    return {
        "external_product_id": external_product_id,
        "external_variant_id": _text(record.get("external_variant_id"), 160),
        "sku": _text(record.get("sku"), 160),
        "barcode": _text(record.get("barcode"), 160),
        "title": _text(record.get("title") or record.get("name"), 500),
        "brand": _text(record.get("brand"), 180),
        "category": _text(record.get("category"), 240),
        "state": _text(record.get("state") or record.get("status"), 80),
        "price": _decimal(record.get("price")),
        "inventory_available": _decimal(record.get("inventory_available", record.get("inventory"))),
        "currency": _text(record.get("currency") or "COP", 3).upper(),
        "url": _text(record.get("url"), 2048),
        "image_url": image_url,
        "payload": record.get("payload") if isinstance(record.get("payload"), dict) else {},
        "observed_at": _datetime(record.get("observed_at"), observed_at),
        "source_updated_at": _datetime(record.get("source_updated_at"), observed_at) if record.get("source_updated_at") else None,
    }


def _rebuild_channel_snapshots(channel):
    ChannelSnapshot.objects.filter(channel=channel).delete()
    exact_rows = ExternalChannelProductSnapshot.objects.filter(
        channel=channel,
        active=True,
        match_status=ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU,
        matched_variant__isnull=False,
    ).select_related("matched_variant__product")
    created = 0
    for row in exact_rows:
        variant = row.matched_variant
        commercial_payload = {
            key: row.payload[key]
            for key in ("shipping_costs", "selling_fees", "profitability")
            if key in (row.payload or {})
        }
        ChannelSnapshot.objects.create(
            product=variant.product,
            variant=variant,
            channel=channel,
            external_product_id=row.external_product_id,
            external_variant_id=row.external_variant_id,
            state=row.state or "UNKNOWN",
            price=row.price,
            inventory_available=row.inventory_available,
            quality_score=80 if row.image_url else 70,
            payload={
                "source": "secure-readonly-channel-import",
                "match": row.match_status,
                "url": row.url,
                "image_url": row.image_url,
                "external_snapshot_id": str(row.id),
                **commercial_payload,
            },
            observed_at=row.observed_at,
        )
        created += 1
    return created


@transaction.atomic
def import_external_channel_snapshot(channel, records, *, observed_at=None, complete=False, source="API read-only"):
    channel = _text(channel).upper()
    if channel not in SUPPORTED_EXTERNAL_CHANNELS:
        raise ChannelImportError("El importador solo acepta Mercado Libre, Falabella o Madecentro.")
    if not isinstance(records, list):
        raise ChannelImportError("El snapshot debe contener una lista records.")
    if complete and not records:
        raise ChannelImportError("Una lectura completa vacía no puede reemplazar el último snapshot correcto.")

    observed_at = _datetime(observed_at, timezone.now())
    normalized = [_normal(record, observed_at) for record in records]
    keys = {(row["external_product_id"], row["external_variant_id"]) for row in normalized}
    if len(keys) != len(normalized):
        raise ChannelImportError("El snapshot contiene identificadores externos duplicados.")

    created = updated = 0
    for row in normalized:
        match_status, matched_variant, candidates, reason = _match_record(row)
        existing = ExternalChannelProductSnapshot.objects.filter(
            channel=channel,
            external_product_id=row["external_product_id"],
            external_variant_id=row["external_variant_id"],
        ).first()
        if existing:
            preserved = {
                key: existing.payload[key]
                for key in ("shipping_costs", "selling_fees", "profitability")
                if key not in row["payload"] and key in (existing.payload or {})
            }
            if preserved:
                row["payload"] = {**preserved, **row["payload"]}
        _, was_created = ExternalChannelProductSnapshot.objects.update_or_create(
            channel=channel,
            external_product_id=row.pop("external_product_id"),
            external_variant_id=row.pop("external_variant_id"),
            defaults={
                **row,
                "matched_variant": matched_variant,
                "match_status": match_status,
                "match_reason": reason,
                "candidate_variant_ids": candidates,
                "active": True,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

    if complete:
        for stale in ExternalChannelProductSnapshot.objects.filter(channel=channel, active=True):
            if (stale.external_product_id, stale.external_variant_id) not in keys:
                stale.active = False
                stale.matched_variant = None
                stale.match_status = ExternalChannelProductSnapshot.MatchStatus.STALE
                stale.match_reason = "No apareció en la lectura completa más reciente; se conserva en historial."
                stale.save(update_fields=["active", "matched_variant", "match_status", "match_reason", "updated_at"])

    duplicate_skus = {
        sku for sku, count in Counter(
            row.sku.casefold() for row in ExternalChannelProductSnapshot.objects.filter(channel=channel, active=True).exclude(sku="")
        ).items() if count > 1
    }
    if duplicate_skus:
        for snapshot in ExternalChannelProductSnapshot.objects.filter(channel=channel, active=True).exclude(sku=""):
            if snapshot.sku.casefold() in duplicate_skus:
                snapshot.match_status = ExternalChannelProductSnapshot.MatchStatus.DUPLICATE_SKU
                snapshot.match_reason = "El mismo SKU aparece en varias publicaciones activas del canal."
                snapshot.matched_variant = None
                snapshot.save(update_fields=["match_status", "match_reason", "matched_variant", "updated_at"])

    linked = _rebuild_channel_snapshots(channel)
    active = ExternalChannelProductSnapshot.objects.filter(channel=channel, active=True)
    summary = {
        "total": active.count(),
        "exact": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU).count(),
        "missing_shopify": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.MISSING_SHOPIFY).count(),
        "missing_sku": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.MISSING_SKU).count(),
        "ambiguous": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.AMBIGUOUS_SKU).count(),
        "duplicates": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.DUPLICATE_SKU).count(),
        "identifier_review": active.filter(match_status=ExternalChannelProductSnapshot.MatchStatus.IDENTIFIER_REVIEW).count(),
        "linked_master_rows": linked,
        "created": created,
        "updated": updated,
        "externalWrites": 0,
    }
    IntegrationReadStatus.objects.update_or_create(
        system=channel,
        capability="marketplace_catalog_snapshot",
        defaults={
            "status": IntegrationReadStatus.Status.AVAILABLE if complete else IntegrationReadStatus.Status.PARTIAL,
            "message": "Catálogo completo persistido en SQLite local." if complete else "Lectura parcial persistida sin reemplazar filas anteriores.",
            "evidence_reference": source[:300],
            "record_count": summary["total"],
            "observed_at": observed_at,
            "last_success_at": observed_at,
            "external_writes": 0,
            "details": summary,
        },
    )
    CatalogHistoryEvent.objects.create(
        entity_type="ExternalChannelProductSnapshot",
        entity_id=channel,
        action="IMPORT_COMPLETE_LOCAL" if complete else "IMPORT_PARTIAL_LOCAL",
        after=summary,
        reversible=True,
        actor_label="readonly-channel-import",
    )
    return summary
