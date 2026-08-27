from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha1

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pedidos.models import (
    IntegrationStatus,
    Order,
    OrderItem,
    Shipment,
    ShipmentItem,
    TrackingEvent,
    WarehouseLocation,
)


ALLOWED_LOGISTICS_STATES = {choice for choice, _ in Shipment.LOGISTICS_STATES}


def _text(value, maximum=600):
    return str(value or "").strip()[:maximum]


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _positive_int(value, default=1):
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _datetime(value):
    parsed = parse_datetime(str(value or ""))
    return parsed or timezone.now()


def _minimal_order_snapshot(row):
    return {
        "canonical_order_id": _text(row.get("id"), 80),
        "source_fingerprint": _text(row.get("source_fingerprint"), 160),
        "last_synchronized_at": row.get("last_synchronized_at"),
        "canonicalImport": True,
        "externalWrites": 0,
    }


def _minimal_shipment_snapshot(row):
    return {
        "canonical_shipment_id": _text(row.get("id"), 80),
        "canonical_external_id": _text(row.get("external_id"), 180),
        "remote_documents": row.get("documents") if isinstance(row.get("documents"), list) else [],
        "guide_review_required": bool(row.get("guide_review_required")),
        "canonicalImport": True,
        "externalWrites": 0,
    }


def _warehouse_for(shipment):
    name = _text(shipment.get("warehouse_name"), 160)
    external_id = _text(shipment.get("warehouse_location_id"), 120)
    if not name:
        return None
    if not external_id:
        digest = sha1(name.casefold().encode("utf-8")).hexdigest()[:20]
        external_id = f"canonical-name:{digest}"
    warehouse, _ = WarehouseLocation.objects.update_or_create(
        external_id=external_id,
        defaults={
            "name": name,
            "reference": _text(shipment.get("warehouse_reference"), 120),
            "active": True,
        },
    )
    return warehouse


@transaction.atomic
def apply_integration_readiness(*, readiness, from_date, to_date):
    readiness = readiness if isinstance(readiness, dict) else {}
    now = timezone.now()
    mapping = {
        "shopify": "shopify",
        "mercadoLibre": "mercado_libre",
        "falabella": "falabella",
        "sodimac": "sodimac",
        "envia": "envia",
    }
    for source_key, provider in mapping.items():
        source = readiness.get(source_key) if isinstance(readiness.get(source_key), dict) else {}
        current, _ = IntegrationStatus.objects.get_or_create(provider=provider)
        state = _text(source.get("state") or "configured_unverified", 60)
        enabled = bool(source.get("enabled"))
        missing = source.get("missingScopes") or source.get("missing") or []
        details = dict(current.details or {})
        details.update(
            {
                "sourceState": state,
                "enabled": enabled,
                "missingCount": len(missing) if isinstance(missing, list) else 0,
                "latestSyncAt": source.get("latestSyncAt"),
                "lastCheckAt": source.get("lastCheckAt"),
                "sourceOrders": source.get("orders"),
                "from": from_date,
                "to": to_date,
                "externalWrites": 0,
            }
        )
        current.state = state
        current.last_attempt_at = now
        if enabled:
            current.last_success_at = now
        current.last_error_code = "SOURCE_REPORTED_ERROR" if source.get("lastError") else ""
        current.details = details
        current.save()
    canonical, _ = IntegrationStatus.objects.get_or_create(provider="pamo_canonical")
    canonical.state = "connected_read_only"
    canonical.last_attempt_at = now
    canonical.last_success_at = now
    canonical.last_error_code = ""
    canonical.details = {
        **(canonical.details or {}),
        "from": from_date,
        "to": to_date,
        "externalWrites": 0,
    }
    canonical.save()


@transaction.atomic
def apply_canonical_snapshot(*, export_payload, details, from_date, to_date):
    rows = export_payload.get("orders") if isinstance(export_payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("La exportación canónica no contiene pedidos.")
    details = details if isinstance(details, dict) else {}
    counts = Counter()
    channel_counts = Counter()
    shipment_records = []

    for summary in rows:
        canonical_id = _text(summary.get("id"), 80)
        detail = details.get(canonical_id) or summary
        channel = _text(detail.get("channel") or summary.get("channel"), 40).lower()
        external_id = _text(detail.get("external_id") or summary.get("external_id") or canonical_id, 160)
        if not channel or not external_id:
            counts["orders_rejected"] += 1
            continue
        visible_id = _text(
            detail.get("channel_order_id")
            or summary.get("channel_order_id")
            or detail.get("order_number")
            or external_id,
            160,
        )
        order, created = Order.objects.get_or_create(
            channel=channel,
            external_id=external_id,
            defaults={
                "visible_id": visible_id,
                "placed_at": _datetime(detail.get("placed_at")),
            },
        )
        order.visible_id = visible_id
        order.source_url = _text(summary.get("channel_order_url"), 600)
        order.placed_at = _datetime(detail.get("placed_at"))
        order.customer_name = _text(detail.get("customer_name"), 240)
        order.customer_email = _text(detail.get("customer_email"), 254)
        order.customer_phone = _text(detail.get("customer_phone"), 40)
        order.currency = _text(detail.get("currency") or "COP", 8)
        order.grand_total = _decimal(detail.get("grand_total"))
        order.state = _text(detail.get("state") or "open", 80)
        order.source_snapshot = _minimal_order_snapshot(detail)
        order.save()
        counts["orders_created" if created else "orders_updated"] += 1
        channel_counts[channel] += 1

        canonical_items = detail.get("items") if isinstance(detail.get("items"), list) else []
        item_by_canonical_id = {}
        for index, item_data in enumerate(canonical_items):
            item_external_id = _text(
                item_data.get("external_id") or item_data.get("id") or f"canonical-item-{index}",
                160,
            )
            item, item_created = OrderItem.objects.update_or_create(
                order=order,
                external_id=item_external_id,
                defaults={
                    "sku": _text(item_data.get("sku"), 160),
                    "name": _text(item_data.get("name") or "Artículo sin nombre", 400),
                    "quantity": _positive_int(item_data.get("quantity")),
                    "unit_price": _decimal(item_data.get("unit_price")),
                    "line_total": _decimal(item_data.get("line_total")),
                    "source_snapshot": {
                        "canonical_item_id": _text(item_data.get("id"), 80),
                        "canonicalImport": True,
                    },
                },
            )
            counts["items_created" if item_created else "items_updated"] += 1
            item_by_canonical_id[_text(item_data.get("id"), 80)] = item

        canonical_shipments = detail.get("shipments") if isinstance(detail.get("shipments"), list) else []
        for shipment_data in canonical_shipments:
            if shipment_data.get("superseded_at"):
                counts["shipments_superseded_skipped"] += 1
                continue
            canonical_shipment_id = _text(shipment_data.get("id"), 80)
            shipment_external_id = _text(
                shipment_data.get("external_id") or f"canonical:{canonical_shipment_id}",
                180,
            )
            shipment, shipment_created = Shipment.objects.get_or_create(
                order=order,
                external_id=shipment_external_id,
            )
            preserve_manual_warehouse = (
                shipment.warehouse_locked
                or shipment.warehouse_assignment_source == "manual"
            )
            preserve_manual_tracking = (
                shipment.tracking_source == "manual" and bool(shipment.tracking_number)
            )
            if not preserve_manual_warehouse:
                warehouse = _warehouse_for(shipment_data)
                shipment.warehouse = warehouse
                shipment.warehouse_name = _text(shipment_data.get("warehouse_name"), 160)
                shipment.warehouse_reference = _text(shipment_data.get("warehouse_reference"), 120)
                shipment.warehouse_locked = bool(shipment_data.get("warehouse_locked"))
                shipment.warehouse_assignment_source = _text(
                    shipment_data.get("warehouse_assignment_source") or "integration", 40
                )
            if not preserve_manual_tracking:
                shipment.tracking_number = _text(shipment_data.get("tracking_number"), 180)
                shipment.tracking_url = _text(shipment_data.get("tracking_url"), 600)
                shipment.tracking_source = _text(
                    shipment_data.get("tracking_source") or ("integration" if shipment.tracking_number else ""),
                    40,
                )
            shipment.carrier = _text(shipment_data.get("carrier"), 120)
            state = _text(shipment_data.get("logistics_state"), 50)
            shipment.logistics_state = state if state in ALLOWED_LOGISTICS_STATES else (
                "guide_without_tracking" if shipment.tracking_number else "without_guide"
            )
            shipment.carrier_state_original = _text(shipment_data.get("carrier_state_original"), 180)
            shipment.carrier_cost = (
                None if shipment_data.get("carrier_cost") is None else _decimal(shipment_data.get("carrier_cost"))
            )
            shipment.carrier_cost_currency = _text(shipment_data.get("carrier_cost_currency"), 8)
            shipment.carrier_cost_source = _text(shipment_data.get("carrier_cost_source"), 80)
            shipment.incident_category = _text(shipment_data.get("incident_category"), 80)
            shipment.incident_detail = _text(shipment_data.get("incident_detail"), 10_000)
            shipment.customer_context = _text(shipment_data.get("customer_context"), 10_000)
            shipment.version = max(int(shipment_data.get("version") or 1), shipment.version or 1)
            shipment.source_snapshot = _minimal_shipment_snapshot(shipment_data)
            shipment.save()
            counts["shipments_created" if shipment_created else "shipments_updated"] += 1
            shipment_records.append(shipment)

            if not preserve_manual_warehouse:
                ShipmentItem.objects.filter(shipment=shipment).delete()
                for allocation in shipment_data.get("items") or []:
                    order_item = item_by_canonical_id.get(_text(allocation.get("order_item_id"), 80))
                    if order_item:
                        ShipmentItem.objects.create(
                            shipment=shipment,
                            order_item=order_item,
                            quantity=_positive_int(allocation.get("quantity")),
                        )

            for event_data in shipment_data.get("tracking_events") or []:
                source = _text(event_data.get("source") or "canonical", 40)
                event_external_id = _text(
                    event_data.get("external_event_id") or event_data.get("id"), 180
                )
                if not event_external_id:
                    continue
                _, event_created = TrackingEvent.objects.update_or_create(
                    shipment=shipment,
                    source=source,
                    external_event_id=event_external_id,
                    defaults={
                        "state_normalized": _text(event_data.get("state_normalized") or "unknown", 60),
                        "state_original": _text(event_data.get("state_original"), 180),
                        "description": _text(event_data.get("description"), 10_000),
                        "occurred_at": _datetime(event_data.get("occurred_at")),
                        "payload": {
                            "canonical_event_id": _text(event_data.get("id"), 80),
                            "location": _text(event_data.get("location"), 240),
                            "canonicalImport": True,
                        },
                    },
                )
                counts["events_created" if event_created else "events_updated"] += 1

    now = timezone.now()
    provider_names = {
        "shopify": "shopify",
        "mercado-libre": "mercado_libre",
        "falabella": "falabella",
        "sodimac": "sodimac",
    }
    for channel, provider in provider_names.items():
        observed = channel_counts.get(channel, 0)
        IntegrationStatus.objects.update_or_create(
            provider=provider,
            defaults={
                "state": "canonical_read_only_import",
                "last_success_at": now,
                "last_attempt_at": now,
                "last_error_code": "",
                "records_observed": observed,
                "details": {
                    "from": from_date,
                    "to": to_date,
                    "canonical": True,
                    "externalWrites": 0,
                },
            },
        )
    IntegrationStatus.objects.update_or_create(
        provider="pamo_canonical",
        defaults={
            "state": "connected_read_only",
            "last_success_at": now,
            "last_attempt_at": now,
            "last_error_code": "",
            "records_observed": sum(channel_counts.values()),
            "details": {
                "from": from_date,
                "to": to_date,
                "channels": dict(channel_counts),
                "externalWrites": 0,
            },
        },
    )
    counts["orders_total"] = sum(channel_counts.values())
    counts["shipments_total"] = len(shipment_records)
    return dict(counts), shipment_records
