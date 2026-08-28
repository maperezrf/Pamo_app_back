import hashlib
import json

from django.conf import settings
from django.db import transaction

from pedidos.functions.messaging import render_message
from pedidos.models import MessagingContact, Shipment

from .interactive import (
    InteractivePayloadError,
    novelty_menu_interactive,
    issue_sku_interactive,
    supplier_order_interactive,
)
from .models import WhatsAppDraft
from .providers import WhatsAppProviderError, normalized_phone


class DraftValidationError(Exception):
    def __init__(self, errors):
        super().__init__("Selección de mensajería inválida.")
        self.errors = errors


def pilot_recipient():
    phone = normalized_phone(settings.PAMO_WHATSAPP_PILOT_RECIPIENT)
    if not phone:
        raise DraftValidationError(
            {"pilot": ["Configura el número de prueba antes de preparar mensajes."]}
        )
    if not phone.endswith("4936"):
        raise DraftValidationError(
            {"pilot": ["El piloto solo permite el número autorizado terminado en 4936."]}
        )
    return phone


def trusted_shopify_warehouse(shipment):
    snapshot = shipment.source_snapshot if isinstance(shipment.source_snapshot, dict) else {}
    location_id = str(snapshot.get("shopify_warehouse_location_id") or "").strip()
    location_name = str(snapshot.get("shopify_warehouse_name") or "").strip()
    if (
        shipment.order.channel != "shopify"
        or not shipment.warehouse_id
        or not location_id
        or not location_name
        or location_id.startswith("canonical-name:")
    ):
        return False
    return (
        shipment.warehouse.external_id == location_id
        and shipment.warehouse.name.strip().casefold() == location_name.casefold()
    )


def _active_contacts(shipment):
    if not trusted_shopify_warehouse(shipment):
        return []
    return list(
        MessagingContact.objects.filter(
            config__warehouse_id=shipment.warehouse_id,
            config__active=True,
            active=True,
        )
        .select_related("config", "config__warehouse")
        .order_by("id")
    )


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
        contacts = _active_contacts(shipment)
        result.append(
            {
                "shipmentId": str(shipment.id),
                "order": shipment.order.visible_id,
                "warehouse": shipment.effective_warehouse_name or "Sin asignar",
                "trustedShopifyWarehouse": trusted_shopify_warehouse(shipment),
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


def _idempotency_key(*, shipment, contact, message_kind, document_sha256=""):
    payload = {
        "source": "pedidos:shipment",
        "shipment": str(shipment.id),
        "contact": str(contact.id),
        "messageKind": message_kind,
        "document": document_sha256,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _contact_reference(contact):
    return f"pedidos.messaging_contact:{contact.id}"


def contact_from_reference(shipment, reference):
    prefix = "pedidos.messaging_contact:"
    value = str(reference or "")
    if not shipment or not value.startswith(prefix):
        return None
    return (
        MessagingContact.objects.filter(
            id=value.removeprefix(prefix),
            active=True,
            config__active=True,
            config__warehouse_id=shipment.warehouse_id,
        )
        .select_related("config", "config__warehouse")
        .first()
    )


def _draft_defaults(*, shipment, contact, message_kind, actor, auto_prepared):
    reference = _contact_reference(contact)
    warehouse = shipment.effective_warehouse_name
    document = getattr(shipment, "document", None) if message_kind == "guide_delivery" else None
    if message_kind == "supplier_order":
        body = render_message(contact.config.template_body, contact.name, warehouse, shipment)
        interactive = supplier_order_interactive(
            shipment_id=shipment.id, contact_reference=reference
        )
    elif message_kind == "guide_delivery":
        body = (
            f"Hola, {contact.name}. Adjuntamos la guía del pedido "
            f"{shipment.order.visible_id}."
        )
        interactive = {}
    elif message_kind == "novelty_menu":
        body = (
            f"Pedido {shipment.order.visible_id}: selecciona el tipo de novedad "
            "que deseas reportar."
        )
        interactive = novelty_menu_interactive(
            shipment_id=shipment.id, contact_reference=reference
        )
    elif message_kind == "novelty_prompt":
        body = "Responde a este mensaje con el detalle solicitado para el despacho."
        interactive = {}
    elif message_kind == "issue_sku_menu":
        body = (
            f"Pedido {shipment.order.visible_id}: selecciona la referencia agotada. "
            "No se marcarán automáticamente los demás SKU."
        )
        try:
            interactive = issue_sku_interactive(
                shipment=shipment, contact_reference=reference
            )
        except InteractivePayloadError as error:
            raise DraftValidationError(
                {"stockout": ["El despacho supera el límite seguro de referencias para WhatsApp."]}
            ) from error
    elif message_kind == "issue_quantity_prompt":
        novelty = shipment.novelties.filter(
            state="open",
            category__in={"supplier_stockout", "supplier_partial", "supplier_damage"},
        ).first()
        affected = (novelty.affected_items or []) if novelty else []
        selected = affected[0] if affected else None
        if not selected:
            raise DraftValidationError(
                {"stockout": ["Primero debe seleccionarse la referencia agotada."]}
            )
        body = (
            f"SKU {selected.get('sku') or 'Sin SKU'}: responde únicamente con la cantidad "
            f"afectada, entre 1 y {selected.get('orderedQuantity')} unidades."
        )
        interactive = {}
    elif message_kind == "novelty_confirmation":
        novelty = shipment.novelties.filter(state="open").first()
        if not novelty or novelty.detail_state != "complete":
            raise DraftValidationError(
                {"novelty": ["La novedad todavía no está completa."]}
            )
        affected = novelty.affected_items or []
        item_summary = ""
        if affected:
            item = affected[0]
            item_summary = (
                f" SKU {item.get('sku') or 'Sin SKU'}, "
                f"{item.get('affectedQuantity')} unidad(es)."
            )
        body = (
            f"Novedad registrada para el pedido {shipment.order.visible_id}. "
            f"Resumen: {novelty.detail}.{item_summary}"
        )
        interactive = {}
    else:
        raise DraftValidationError({"message_kind": ["Tipo de mensaje no soportado."]})
    return {
        "source_module": "pedidos",
        "source_type": "shipment",
        "source_id": str(shipment.id),
        "message_kind": message_kind,
        "order_visible_id": shipment.order.visible_id,
        "warehouse_reference": f"shopify:{shipment.warehouse.external_id}|{warehouse}",
        "contact_reference": reference,
        "recipient_name": f"Piloto · {contact.name}",
        "recipient_phone": pilot_recipient(),
        "rendered_body": body,
        "interactive_payload": interactive,
        "auto_prepared": auto_prepared,
        "document_source_id": str(shipment.id) if document else "",
        "document_name": document.original_name if document else "",
        "document_sha256": document.sha256 if document else "",
        "created_by": actor,
    }


@transaction.atomic
def create_workflow_draft(
    *, shipment, contact, message_kind, actor, auto_prepared=False
):
    if not trusted_shopify_warehouse(shipment):
        raise DraftValidationError(
            {"warehouse": ["La ubicación de Shopify no es verificable para este despacho."]}
        )
    if not contact or contact.config.warehouse_id != shipment.warehouse_id or not contact.active:
        raise DraftValidationError(
            {"contact": ["El contacto activo no pertenece a la ubicación del despacho."]}
        )
    document = getattr(shipment, "document", None) if message_kind == "guide_delivery" else None
    if message_kind == "guide_delivery" and not document:
        raise DraftValidationError({"document": ["La guía todavía no está disponible."]})
    key = _idempotency_key(
        shipment=shipment,
        contact=contact,
        message_kind=message_kind,
        document_sha256=document.sha256 if document else "",
    )
    return WhatsAppDraft.objects.get_or_create(
        idempotency_key=key,
        defaults=_draft_defaults(
            shipment=shipment,
            contact=contact,
            message_kind=message_kind,
            actor=actor,
            auto_prepared=auto_prepared,
        ),
    )


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
        contact = contact_from_reference(
            shipment, f"pedidos.messaging_contact:{contact_id}"
        )
        if not shipment or not contact:
            raise DraftValidationError(
                {"selections": ["Despacho o contacto activo no válido."]}
            )
        draft, created = create_workflow_draft(
            shipment=shipment,
            contact=contact,
            message_kind="supplier_order",
            actor=actor,
        )
        created_count += int(created)
        drafts.append(draft)
    return drafts, created_count


def _approve_enqueue_dispatch(draft, *, actor):
    from .services import approve_draft, dispatch_outbox, enqueue_draft

    approve_draft(draft_id=draft.id, actor=actor)
    outbox, _ = enqueue_draft(draft_id=draft.id)
    try:
        outbox, dispatched = dispatch_outbox(outbox_id=outbox.id)
        return outbox, dispatched, None
    except WhatsAppProviderError as error:
        return outbox, False, error.code


def auto_prepare_new_shipments(shipments, *, actor="system:canonical-import"):
    result = {
        "eligible": 0,
        "created": 0,
        "reused": 0,
        "dispatched": 0,
        "skippedUntrustedWarehouse": 0,
        "skippedNoContacts": 0,
        "skippedPilotUnavailable": 0,
        "errors": {},
    }
    if not (
        settings.PAMO_WHATSAPP_AUTO_PREPARE_ENABLED
        and settings.PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED
    ):
        return result
    try:
        pilot_recipient()
    except DraftValidationError:
        result["skippedPilotUnavailable"] = len(shipments)
        return result
    for raw_shipment in shipments:
        shipment = (
            Shipment.objects.filter(id=raw_shipment.id)
            .select_related("order", "warehouse")
            .prefetch_related("shipment_items__order_item")
            .first()
        )
        if not shipment or not trusted_shopify_warehouse(shipment):
            result["skippedUntrustedWarehouse"] += 1
            continue
        contacts = _active_contacts(shipment)
        if not contacts:
            result["skippedNoContacts"] += 1
            continue
        result["eligible"] += 1
        for contact in contacts:
            draft, created = create_workflow_draft(
                shipment=shipment,
                contact=contact,
                message_kind="supplier_order",
                actor=actor,
                auto_prepared=True,
            )
            result["created" if created else "reused"] += 1
            _, dispatched, error_code = _approve_enqueue_dispatch(draft, actor=actor)
            result["dispatched"] += int(dispatched)
            if error_code:
                result["errors"][error_code] = result["errors"].get(error_code, 0) + 1
    return result


def prepare_and_dispatch_workflow(
    *, shipment, contact_reference, message_kind, actor="system:whatsapp-webhook"
):
    contact = contact_from_reference(shipment, contact_reference)
    if not contact:
        raise DraftValidationError({"contact": ["Contacto del despacho no disponible."]})
    draft, created = create_workflow_draft(
        shipment=shipment,
        contact=contact,
        message_kind=message_kind,
        actor=actor,
        auto_prepared=True,
    )
    outbox, dispatched, error_code = _approve_enqueue_dispatch(draft, actor=actor)
    return {
        "draft": draft,
        "outbox": outbox,
        "created": created,
        "dispatched": dispatched,
        "errorCode": error_code,
    }


def dispatch_requested_guides(shipments, *, actor="system:guide-available"):
    from pedidos.models import SupplierResponseEvent

    result = {"prepared": 0, "dispatched": 0, "errors": {}}
    if not settings.PAMO_WHATSAPP_GUIDE_AUTO_SEND_ENABLED:
        return result
    for raw_shipment in shipments:
        shipment = (
            Shipment.objects.filter(id=raw_shipment.id)
            .select_related("order", "warehouse", "document")
            .first()
        )
        if not shipment or not hasattr(shipment, "document"):
            continue
        request = (
            SupplierResponseEvent.objects.filter(
                shipment=shipment, action="request_guide", result="applied"
            )
            .order_by("occurred_at")
            .first()
        )
        contact_reference = (request.details or {}).get("contactReference") if request else ""
        if not contact_reference:
            continue
        try:
            delivery = prepare_and_dispatch_workflow(
                shipment=shipment,
                contact_reference=contact_reference,
                message_kind="guide_delivery",
                actor=actor,
            )
        except DraftValidationError:
            continue
        result["prepared"] += int(delivery["created"])
        result["dispatched"] += int(delivery["dispatched"])
        if delivery["errorCode"]:
            code = delivery["errorCode"]
            result["errors"][code] = result["errors"].get(code, 0) + 1
        if delivery["outbox"].state in {"sent", "delivered", "read"}:
            shipment.guide_delivery_state = "sent"
            shipment.save(update_fields=["guide_delivery_state", "updated_at"])
    return result
