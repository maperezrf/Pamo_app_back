from django.db import transaction
from django.utils import timezone

from pedidos.models import (
    LogisticsAudit,
    MessagingContact,
    Shipment,
    ShipmentItem,
    ShipmentNovelty,
    SupplierResponseEvent,
)


class SupplierResponseError(Exception):
    def __init__(self, code, http_status=400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def _authorized_contact(shipment, sender_phone):
    expected = {
        _digits(phone)
        for phone in MessagingContact.objects.filter(
            config__warehouse_id=shipment.warehouse_id,
            config__active=True,
            active=True,
        ).values_list("phone", flat=True)
    }
    return bool(expected and _digits(sender_phone) in expected)


def _issue_item_payload(shipment_item):
    return {
        "shipmentItemId": str(shipment_item.id),
        "sku": shipment_item.order_item.sku,
        "name": shipment_item.order_item.name,
        "orderedQuantity": shipment_item.quantity,
        "affectedQuantity": None,
        "scope": "pending",
    }


def _payload(shipment, event, *, replayed=False):
    novelty = ShipmentNovelty.objects.filter(
        shipment=shipment, state="open"
    ).first()
    return {
        "eventId": str(event.id),
        "replayed": replayed,
        "result": event.result,
        "supplierState": shipment.supplier_state,
        "guideDeliveryState": shipment.guide_delivery_state,
        "guideAvailable": hasattr(shipment, "document"),
        "nextPrompt": event.details.get("nextPrompt") if event.details else None,
        "openNovelty": (
            {
                "id": str(novelty.id),
                "category": novelty.category,
                "detailState": novelty.detail_state,
                "affectedItems": novelty.affected_items,
            }
            if novelty
            else None
        ),
    }


@transaction.atomic
def apply_supplier_response(
    *, shipment_id, action, provider_event_id, sender_phone, source="whatsapp",
    validate_contact=True, contact_reference=""
):
    existing = SupplierResponseEvent.objects.filter(
        provider_event_id=provider_event_id
    ).select_related("shipment").first()
    if existing:
        return _payload(existing.shipment, existing, replayed=True)

    shipment = (
        Shipment.objects.select_for_update()
        .select_related("warehouse")
        .filter(id=shipment_id)
        .first()
    )
    if not shipment:
        raise SupplierResponseError("shipment_not_found", 404)
    if action not in {
        "order_received",
        "request_guide",
        "report_stockout",
        "report_issue",
    }:
        raise SupplierResponseError("unsupported_supplier_action")
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)

    previous = shipment.supplier_state
    new_state = previous
    result = "applied"
    details = {
        "warehouseId": str(shipment.warehouse_id or ""),
        "warehouse": shipment.effective_warehouse_name,
    }
    if contact_reference:
        details["contactReference"] = contact_reference
    open_novelty = ShipmentNovelty.objects.filter(
        shipment=shipment, state="open"
    ).exists()

    first_response = (
        SupplierResponseEvent.objects.filter(
            shipment=shipment,
            action__in={
                "order_received",
                "request_guide",
                "report_stockout",
                "report_issue",
            },
            result="applied",
        )
        .order_by("occurred_at")
        .first()
    )
    if first_response:
        result = "replayed" if first_response.action == action else "review"
        details.update(
            {
                "reason": (
                    "same_primary_response_already_applied"
                    if result == "replayed"
                    else "conflicts_with_first_primary_response"
                ),
                "firstResponseEventId": str(first_response.id),
                "firstResponseAction": first_response.action,
            }
        )

    if first_response:
        pass
    elif action == "order_received":
        if previous == "pending_response":
            new_state = "received"
        elif previous in {"ready_for_guide", "issue_reported"}:
            result = "review"
            details["reason"] = "state_would_move_backwards"
    elif action == "request_guide":
        if open_novelty:
            result = "review"
            details["reason"] = "open_novelty_requires_resolution"
        else:
            new_state = "ready_for_guide"
            shipment.guide_delivery_state = (
                "ready_to_send" if hasattr(shipment, "document") else "requested"
            )
            details["guideAction"] = (
                "send_existing" if hasattr(shipment, "document") else "await_generation"
            )
    elif action in {"report_stockout", "report_issue"}:
        if open_novelty:
            result = "review"
            details["reason"] = "open_novelty_already_exists"
        else:
            new_state = "issue_reported"

    event = SupplierResponseEvent.objects.create(
        shipment=shipment,
        provider_event_id=provider_event_id,
        action=action,
        source=source,
        sender_suffix=_digits(sender_phone)[-4:],
        previous_state=previous,
        new_state=new_state,
        result=result,
        details=details,
    )
    if action in {"report_stockout", "report_issue"} and not first_response and not open_novelty:
        if action == "report_stockout":
            items = list(
                shipment.shipment_items.select_related("order_item").order_by("id")
            )
            affected_items = []
            detail_state = "awaiting_item"
            detail = "Selecciona la referencia agotada; los demás SKU no se modificarán."
            if len(items) == 1:
                item = items[0]
                affected_items = [_issue_item_payload(item)]
                detail_state = "awaiting_quantity"
                detail = "Confirma la cantidad afectada para la única referencia del despacho."
            ShipmentNovelty.objects.create(
                shipment=shipment,
                supplier_response=event,
                category="supplier_stockout",
                detail_state=detail_state,
                detail=detail,
                affected_items=affected_items,
            )
        else:
            ShipmentNovelty.objects.create(
                shipment=shipment,
                supplier_response=event,
                category="supplier_pending_detail",
                detail_state="awaiting_category",
                detail="El proveedor reportó una novedad y debe indicar el tipo y los SKU afectados.",
            )

    changed = new_state != previous
    if changed:
        shipment.supplier_state = new_state
        shipment.supplier_state_updated_at = timezone.now()
    if changed or (action == "request_guide" and not first_response and result == "applied"):
        shipment.version += 1
        shipment.save(
            update_fields=[
                "supplier_state",
                "supplier_state_updated_at",
                "guide_delivery_state",
                "version",
                "updated_at",
            ]
        )
        LogisticsAudit.objects.create(
            shipment=shipment,
            field="supplier_response",
            previous_value=previous,
            new_value=new_state,
            actor=f"proveedor:***{_digits(sender_phone)[-4:]}",
            source=source,
            detail=action,
        )
    return _payload(shipment, event)


NOVELTY_CATEGORY_PROMPTS = {
    "supplier_stockout": "Selecciona el SKU agotado y luego confirma la cantidad afectada.",
    "supplier_partial": "Selecciona el SKU con cantidad incompleta y confirma cuántas unidades faltan.",
    "supplier_damage": "Selecciona el SKU averiado y confirma cuántas unidades están afectadas.",
    "supplier_guide_issue": "Describe brevemente el problema con la guía.",
    "supplier_delay": "Indica la nueva fecha estimada de despacho.",
    "supplier_not_recognized": "Registramos que no reconoces este pedido. PAMO lo revisará antes de continuar.",
    "supplier_other": "Describe brevemente la novedad e indica los SKU afectados.",
}


@transaction.atomic
def apply_supplier_issue_item(
    *, shipment_id, shipment_item_id, provider_event_id, sender_phone,
    source="whatsapp", validate_contact=True
):
    existing = SupplierResponseEvent.objects.filter(
        provider_event_id=provider_event_id
    ).select_related("shipment").first()
    if existing:
        return _payload(existing.shipment, existing, replayed=True)

    shipment = (
        Shipment.objects.select_for_update()
        .select_related("warehouse")
        .filter(id=shipment_id)
        .first()
    )
    if not shipment:
        raise SupplierResponseError("shipment_not_found", 404)
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)
    novelty = (
        ShipmentNovelty.objects.select_for_update()
        .filter(
            shipment=shipment,
            state="open",
            category__in={"supplier_stockout", "supplier_partial", "supplier_damage"},
            detail_state="awaiting_item",
        )
        .first()
    )
    if not novelty:
        raise SupplierResponseError("issue_item_selection_not_expected", 409)
    shipment_item = (
        ShipmentItem.objects.select_related("order_item")
        .filter(id=shipment_item_id, shipment=shipment)
        .first()
    )
    if not shipment_item:
        raise SupplierResponseError("issue_item_not_in_shipment", 400)

    affected = _issue_item_payload(shipment_item)
    novelty.affected_items = [affected]
    novelty.detail_state = "awaiting_quantity"
    novelty.detail = (
        f"Confirma la cantidad afectada del SKU {affected['sku'] or 'Sin SKU'}."
    )
    novelty.save(update_fields=["affected_items", "detail_state", "detail"])
    event = SupplierResponseEvent.objects.create(
        shipment=shipment,
        provider_event_id=provider_event_id,
        action="select_issue_item",
        source=source,
        sender_suffix=_digits(sender_phone)[-4:],
        previous_state=shipment.supplier_state,
        new_state=shipment.supplier_state,
        result="applied",
        details={
            "warehouseId": str(shipment.warehouse_id or ""),
            "warehouse": shipment.effective_warehouse_name,
            "shipmentItemId": str(shipment_item.id),
            "sku": affected["sku"],
            "orderedQuantity": affected["orderedQuantity"],
        },
    )
    shipment.version += 1
    shipment.save(update_fields=["version", "updated_at"])
    LogisticsAudit.objects.create(
        shipment=shipment,
        field="supplier_issue_item",
        previous_value="",
        new_value=str(shipment_item.id),
        actor=f"proveedor:***{_digits(sender_phone)[-4:]}",
        source=source,
        detail=affected["sku"] or "Sin SKU",
    )
    return _payload(shipment, event)


@transaction.atomic
def apply_supplier_issue_quantity(
    *, shipment_id, quantity, provider_event_id, sender_phone,
    source="whatsapp", validate_contact=True
):
    existing = SupplierResponseEvent.objects.filter(
        provider_event_id=provider_event_id
    ).select_related("shipment").first()
    if existing:
        return _payload(existing.shipment, existing, replayed=True)
    value = str(quantity or "").strip()
    if not value.isdigit() or int(value) <= 0:
        raise SupplierResponseError("issue_quantity_invalid")
    affected_quantity = int(value)

    shipment = (
        Shipment.objects.select_for_update()
        .select_related("warehouse")
        .filter(id=shipment_id)
        .first()
    )
    if not shipment:
        raise SupplierResponseError("shipment_not_found", 404)
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)
    novelty = (
        ShipmentNovelty.objects.select_for_update()
        .filter(
            shipment=shipment,
            state="open",
            category__in={"supplier_stockout", "supplier_partial", "supplier_damage"},
            detail_state="awaiting_quantity",
        )
        .first()
    )
    if not novelty or len(novelty.affected_items or []) != 1:
        raise SupplierResponseError("issue_quantity_not_expected", 409)
    affected = dict(novelty.affected_items[0])
    ordered_quantity = int(affected.get("orderedQuantity") or 0)
    if affected_quantity > ordered_quantity:
        raise SupplierResponseError("issue_quantity_exceeds_shipment", 400)
    affected["affectedQuantity"] = affected_quantity
    affected["scope"] = "total" if affected_quantity == ordered_quantity else "partial"
    novelty.affected_items = [affected]
    novelty.detail_state = "complete"
    novelty.detail = (
        f"SKU {affected.get('sku') or 'Sin SKU'}: {affected_quantity} de "
        f"{ordered_quantity} unidad(es) afectadas."
    )
    novelty.save(update_fields=["affected_items", "detail_state", "detail"])
    event = SupplierResponseEvent.objects.create(
        shipment=shipment,
        provider_event_id=provider_event_id,
        action="provide_issue_quantity",
        source=source,
        sender_suffix=_digits(sender_phone)[-4:],
        previous_state=shipment.supplier_state,
        new_state=shipment.supplier_state,
        result="applied",
        details={
            "warehouseId": str(shipment.warehouse_id or ""),
            "warehouse": shipment.effective_warehouse_name,
            "shipmentItemId": affected["shipmentItemId"],
            "sku": affected.get("sku"),
            "orderedQuantity": ordered_quantity,
            "affectedQuantity": affected_quantity,
            "scope": affected["scope"],
        },
    )
    shipment.version += 1
    shipment.save(update_fields=["version", "updated_at"])
    LogisticsAudit.objects.create(
        shipment=shipment,
        field="supplier_issue_quantity",
        previous_value="",
        new_value=str(affected_quantity),
        actor=f"proveedor:***{_digits(sender_phone)[-4:]}",
        source=source,
        detail=f"{affected.get('sku') or 'Sin SKU'}:{affected['scope']}",
    )
    return _payload(shipment, event)


@transaction.atomic
def apply_supplier_novelty_category(
    *, shipment_id, category, provider_event_id, sender_phone, source="whatsapp", validate_contact=True
):
    existing = SupplierResponseEvent.objects.filter(
        provider_event_id=provider_event_id
    ).select_related("shipment").first()
    if existing:
        return _payload(existing.shipment, existing, replayed=True)
    if category not in NOVELTY_CATEGORY_PROMPTS:
        raise SupplierResponseError("unsupported_supplier_novelty_category")

    shipment = (
        Shipment.objects.select_for_update()
        .select_related("warehouse")
        .filter(id=shipment_id)
        .first()
    )
    if not shipment:
        raise SupplierResponseError("shipment_not_found", 404)
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)
    novelty = (
        ShipmentNovelty.objects.select_for_update()
        .filter(shipment=shipment, state="open")
        .first()
    )
    if not novelty:
        raise SupplierResponseError("open_supplier_novelty_missing", 409)
    if novelty.detail_state != "awaiting_category":
        raise SupplierResponseError("supplier_novelty_category_already_recorded", 409)

    previous_category = novelty.category
    novelty.category = category
    if category in {"supplier_stockout", "supplier_partial", "supplier_damage"}:
        novelty.detail_state = "awaiting_item"
    elif category == "supplier_not_recognized":
        novelty.detail_state = "complete"
    else:
        novelty.detail_state = "awaiting_detail"
    novelty.detail = NOVELTY_CATEGORY_PROMPTS[category]
    novelty.save(update_fields=["category", "detail_state", "detail"])

    event = SupplierResponseEvent.objects.create(
        shipment=shipment,
        provider_event_id=provider_event_id,
        action="classify_issue",
        source=source,
        sender_suffix=_digits(sender_phone)[-4:],
        previous_state=shipment.supplier_state,
        new_state=shipment.supplier_state,
        result="applied",
        details={
            "noveltyId": str(novelty.id),
            "previousCategory": previous_category,
            "category": category,
            "detailState": novelty.detail_state,
            "nextPrompt": NOVELTY_CATEGORY_PROMPTS[category],
        },
    )
    shipment.version += 1
    shipment.save(update_fields=["version", "updated_at"])
    LogisticsAudit.objects.create(
        shipment=shipment,
        field="supplier_novelty_category",
        previous_value=previous_category,
        new_value=category,
        actor=f"proveedor:***{_digits(sender_phone)[-4:]}",
        source=source,
        detail=novelty.detail_state,
    )
    return _payload(shipment, event)


@transaction.atomic
def apply_supplier_novelty_detail(
    *, shipment_id, detail, provider_event_id, sender_phone, source="whatsapp", validate_contact=True
):
    existing = SupplierResponseEvent.objects.filter(
        provider_event_id=provider_event_id
    ).select_related("shipment").first()
    if existing:
        return _payload(existing.shipment, existing, replayed=True)
    detail = " ".join(str(detail or "").split()).strip()
    if not detail:
        raise SupplierResponseError("supplier_novelty_detail_missing")
    if len(detail) > 2000:
        raise SupplierResponseError("supplier_novelty_detail_too_long")

    shipment = (
        Shipment.objects.select_for_update()
        .select_related("warehouse")
        .filter(id=shipment_id)
        .first()
    )
    if not shipment:
        raise SupplierResponseError("shipment_not_found", 404)
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)
    novelty = (
        ShipmentNovelty.objects.select_for_update()
        .filter(shipment=shipment, state="open", detail_state="awaiting_detail")
        .first()
    )
    if not novelty:
        raise SupplierResponseError("supplier_novelty_awaiting_detail_missing", 409)

    previous_detail = novelty.detail
    novelty.detail = detail
    novelty.detail_state = "complete"
    novelty.save(update_fields=["detail", "detail_state"])
    event = SupplierResponseEvent.objects.create(
        shipment=shipment,
        provider_event_id=provider_event_id,
        action="provide_issue_detail",
        source=source,
        sender_suffix=_digits(sender_phone)[-4:],
        previous_state=shipment.supplier_state,
        new_state=shipment.supplier_state,
        result="applied",
        details={
            "noveltyId": str(novelty.id),
            "category": novelty.category,
            "detailState": novelty.detail_state,
        },
    )
    shipment.version += 1
    shipment.save(update_fields=["version", "updated_at"])
    LogisticsAudit.objects.create(
        shipment=shipment,
        field="supplier_novelty_detail",
        previous_value=previous_detail,
        new_value=detail,
        actor=f"proveedor:***{_digits(sender_phone)[-4:]}",
        source=source,
        detail=novelty.category,
    )
    return _payload(shipment, event)
