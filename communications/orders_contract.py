import hashlib
import json

from django.db import transaction

from pedidos.functions.messaging import render_message
from pedidos.models import MessagingContact, Shipment

from .models import WhatsAppDraft


class DraftValidationError(Exception):
    def __init__(self, errors):
        super().__init__("Selección de mensajería inválida.")
        self.errors = errors


def recipient_options(shipment_ids):
    shipments = list(
        Shipment.objects.filter(id__in=shipment_ids)
        .select_related("order", "warehouse")
        .order_by("order__placed_at", "id")
    )
    found_ids = {str(item.id) for item in shipments}
    missing = [str(item) for item in shipment_ids if str(item) not in found_ids]
    if missing:
        raise DraftValidationError({"shipment_ids": ["Hay despachos inexistentes."]})
    result = []
    for shipment in shipments:
        contacts = []
        if shipment.warehouse_id:
            contacts = list(
                MessagingContact.objects.filter(
                    config__warehouse_id=shipment.warehouse_id,
                    config__active=True,
                    active=True,
                ).order_by("id")
            )
        result.append(
            {
                "shipmentId": str(shipment.id),
                "order": shipment.order.visible_id,
                "warehouse": shipment.effective_warehouse_name or "Sin asignar",
                "contacts": [
                    {
                        "id": str(contact.id),
                        "name": contact.name,
                        "phoneMasked": f"••••{contact.phone[-4:]}",
                    }
                    for contact in contacts
                ],
                "hasDocument": hasattr(shipment, "document"),
                "guide": shipment.tracking_number or None,
            }
        )
    return result


def _idempotency_key(*, shipment, contact, body, document_sha256):
    payload = {
        "source": "pedidos:shipment",
        "shipment": str(shipment.id),
        "shipmentVersion": shipment.version,
        "contact": str(contact.id),
        "body": hashlib.sha256(body.encode()).hexdigest(),
        "document": document_sha256,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@transaction.atomic
def create_order_drafts(*, selections, actor):
    if not isinstance(selections, list) or not selections:
        raise DraftValidationError({"selections": ["Selecciona al menos un contacto."]})
    unique_pairs = set()
    drafts = []
    created_count = 0
    for selection in selections:
        shipment_id = str(selection.get("shipment_id", "")).strip()
        contact_id = str(selection.get("contact_id", "")).strip()
        pair = (shipment_id, contact_id)
        if not shipment_id or not contact_id or pair in unique_pairs:
            raise DraftValidationError(
                {"selections": ["Cada despacho/contacto debe ser explícito y no repetido."]}
            )
        unique_pairs.add(pair)
        shipment = (
            Shipment.objects.filter(id=shipment_id)
            .select_related("order", "warehouse")
            .prefetch_related("shipment_items__order_item")
            .first()
        )
        if not shipment or not shipment.warehouse_id:
            raise DraftValidationError(
                {"selections": ["El despacho debe existir y tener bodega asignada."]}
            )
        contact = (
            MessagingContact.objects.filter(
                id=contact_id,
                active=True,
                config__active=True,
                config__warehouse_id=shipment.warehouse_id,
            )
            .select_related("config", "config__warehouse")
            .first()
        )
        if not contact:
            raise DraftValidationError(
                {"selections": ["El contacto no pertenece a la bodega de ese despacho."]}
            )
        body = render_message(
            contact.config.template_body,
            contact.name,
            shipment.effective_warehouse_name,
            shipment,
        )
        document = getattr(shipment, "document", None)
        document_sha256 = document.sha256 if document else ""
        key = _idempotency_key(
            shipment=shipment,
            contact=contact,
            body=body,
            document_sha256=document_sha256,
        )
        draft, created = WhatsAppDraft.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "source_module": "pedidos",
                "source_type": "shipment",
                "source_id": str(shipment.id),
                "order_visible_id": shipment.order.visible_id,
                "warehouse_reference": shipment.effective_warehouse_name,
                "contact_reference": f"pedidos.messaging_contact:{contact.id}",
                "recipient_name": contact.name,
                "recipient_phone": contact.phone,
                "rendered_body": body,
                "document_source_id": str(shipment.id) if document else "",
                "document_name": document.original_name if document else "",
                "document_sha256": document_sha256,
                "created_by": actor,
            },
        )
        created_count += int(created)
        drafts.append(draft)
    return drafts, created_count
