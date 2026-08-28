from django.db import transaction
from django.utils import timezone

from pedidos.models import (
    LogisticsAudit,
    MessagingContact,
    Shipment,
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
        "openNovelty": (
            {"id": str(novelty.id), "category": novelty.category}
            if novelty
            else None
        ),
    }


@transaction.atomic
def apply_supplier_response(
    *, shipment_id, action, provider_event_id, sender_phone, source="whatsapp", validate_contact=True
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
    if action not in {"order_received", "request_guide", "report_issue"}:
        raise SupplierResponseError("unsupported_supplier_action")
    if validate_contact and not _authorized_contact(shipment, sender_phone):
        raise SupplierResponseError("supplier_contact_mismatch", 403)

    previous = shipment.supplier_state
    new_state = previous
    result = "applied"
    details = {}
    open_novelty = ShipmentNovelty.objects.filter(
        shipment=shipment, state="open"
    ).exists()

    if action == "order_received":
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
    if action == "report_issue" and not open_novelty:
        ShipmentNovelty.objects.create(
            shipment=shipment,
            supplier_response=event,
            category="supplier_pending_detail",
            detail="El proveedor reporto una novedad y debe indicar el tipo y los SKU afectados.",
        )

    changed = new_state != previous
    if changed:
        shipment.supplier_state = new_state
        shipment.supplier_state_updated_at = timezone.now()
    if changed or action == "request_guide":
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
