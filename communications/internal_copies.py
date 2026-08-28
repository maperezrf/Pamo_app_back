import hashlib
import json
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pedidos.models import Order

from .models import WhatsAppDraft
from .providers import WhatsAppProviderError, normalized_phone
from .services import approve_draft, dispatch_outbox, enqueue_draft


@dataclass(frozen=True)
class InternalRecipient:
    name: str
    phone: str
    suffix: str
    active: bool
    fingerprint: str


def _fingerprint(name, phone, suffix):
    material = f"{name.strip().casefold()}|{phone or suffix}"
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def internal_recipients():
    recipients = []
    pilot_phone = normalized_phone(settings.PAMO_WHATSAPP_PILOT_RECIPIENT)
    pilot_name = str(settings.PAMO_WHATSAPP_PILOT_RECIPIENT_NAME or "Piloto autorizado").strip()
    if pilot_phone.endswith("4936"):
        recipients.append(
            InternalRecipient(
                name=pilot_name,
                phone=pilot_phone,
                suffix=pilot_phone[-4:],
                active=True,
                fingerprint=_fingerprint(pilot_name, pilot_phone, pilot_phone[-4:]),
            )
        )
    return recipients


def serialized_internal_recipients():
    return [
        {
            "name": item.name,
            "phoneMasked": f"••••{item.suffix}" if item.suffix else "Pendiente",
            "active": item.active,
            "configured": bool(item.phone),
        }
        for item in internal_recipients()
    ]


def internal_copy_checkpoint():
    value = str(settings.PAMO_WHATSAPP_INTERNAL_COPY_FROM or "").strip()
    parsed = parse_datetime(value) if value else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _render_order_summary(order):
    lines = [f"Nuevo pedido {order.visible_id}", f"Canal: {order.channel}"]
    for shipment in order.shipments.all():
        warehouse = shipment.effective_warehouse_name or "Sin bodega verificada"
        lines.extend(["", f"Despacho · {warehouse}"])
        for item in shipment.shipment_items.all():
            order_item = item.order_item
            lines.append(
                f"SKU {order_item.sku or 'Sin SKU'} · {order_item.name} · {item.quantity} unidad(es)"
            )
        lines.append(f"Guía: {shipment.tracking_number or 'Pendiente'}")
        lines.append(f"Estado: {shipment.get_logistics_state_display()}")
        if shipment.incident_category or shipment.incident_detail:
            lines.append(
                f"Novedad: {shipment.incident_detail or shipment.incident_category}"
            )
    return "\n".join(lines)


def _idempotency_key(order, recipient):
    material = {
        "channel": order.channel,
        "externalOrderId": order.external_id,
        "event": "order.created",
        "recipient": recipient.fingerprint,
        "template": settings.PAMO_WHATSAPP_INTERNAL_TEMPLATE_VERSION,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


@transaction.atomic
def _create_draft(order, recipient):
    key = _idempotency_key(order, recipient)
    return WhatsAppDraft.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "source_module": "pedidos",
            "source_type": "order",
            "source_id": str(order.id),
            "message_kind": "internal_order_copy",
            "order_visible_id": order.visible_id,
            "warehouse_reference": "pedido_multidespacho",
            "contact_reference": f"internal:{recipient.fingerprint}",
            "recipient_name": recipient.name,
            "recipient_phone": recipient.phone,
            "rendered_body": _render_order_summary(order),
            "interactive_payload": {},
            "auto_prepared": True,
            "created_by": "system:canonical-import-internal-copy",
        },
    )


def auto_send_internal_order_copies(orders, *, client_factory=None):
    result = {
        "eligibleOrders": 0,
        "created": 0,
        "reused": 0,
        "dispatched": 0,
        "skippedBeforeCheckpoint": 0,
        "skippedNoRecipients": 0,
        "skippedAutomationDisabled": 0,
        "skippedUnsafeEnvironment": 0,
        "recipientFailures": {},
    }
    unique_order_ids = {item.id for item in orders}
    if not settings.PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED:
        result["skippedAutomationDisabled"] = len(unique_order_ids)
        return result
    if settings.PAMO_WHATSAPP_DEPLOYMENT_TIER not in {"local", "staging-whatsapp"}:
        result["skippedUnsafeEnvironment"] = len(unique_order_ids)
        return result
    checkpoint = internal_copy_checkpoint()
    recipients = [item for item in internal_recipients() if item.active and item.phone]
    if not checkpoint:
        result["skippedBeforeCheckpoint"] = len(unique_order_ids)
        return result
    if not recipients:
        result["skippedNoRecipients"] = len(unique_order_ids)
        return result
    queryset = (
        Order.objects.filter(id__in=unique_order_ids, channel="shopify")
        .prefetch_related("shipments__shipment_items__order_item")
        .order_by("placed_at", "id")
    )
    for order in queryset:
        if order.placed_at < checkpoint:
            result["skippedBeforeCheckpoint"] += 1
            continue
        result["eligibleOrders"] += 1
        for recipient in recipients:
            draft, created = _create_draft(order, recipient)
            result["created" if created else "reused"] += 1
            approve_draft(
                draft_id=draft.id, actor="system:canonical-import-internal-copy"
            )
            outbox, _ = enqueue_draft(draft_id=draft.id)
            try:
                client = client_factory(recipient) if client_factory else None
                _, dispatched = dispatch_outbox(outbox_id=outbox.id, client=client)
                result["dispatched"] += int(dispatched)
            except WhatsAppProviderError as error:
                result["recipientFailures"][recipient.fingerprint] = error.code
    return result
