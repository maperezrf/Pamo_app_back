import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from pedidos.functions.supplier_responses import (
    SupplierResponseError,
    apply_supplier_issue_item,
    apply_supplier_issue_quantity,
    apply_supplier_novelty_category,
    apply_supplier_novelty_detail,
    apply_supplier_response,
)
from pedidos.models import Shipment

from .interactive import InteractivePayloadError, parse_signed_action
from .models import WhatsAppWebhookEvent, WhatsAppOutbox
from .orders_contract import DraftValidationError, prepare_and_dispatch_workflow
from .providers import normalized_phone


class WebhookValidationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def verify_signature(raw_body, signature_header):
    if not settings.META_APP_SECRET or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        settings.META_APP_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def verify_challenge(*, mode, token, challenge):
    if mode != "subscribe" or not settings.META_VERIFY_TOKEN:
        raise WebhookValidationError("WEBHOOK_VERIFY_DISABLED", "Verificación no configurada.")
    if not hmac.compare_digest(str(token or ""), settings.META_VERIFY_TOKEN):
        raise WebhookValidationError("WEBHOOK_VERIFY_TOKEN_INVALID", "Token inválido.")
    return str(challenge or "")


def _extract_events(payload):
    events = []
    for entry in payload.get("entry", []):
        waba_id = str(entry.get("id", ""))
        if not settings.META_WABA_ID or waba_id != settings.META_WABA_ID:
            raise WebhookValidationError("WABA_ID_MISMATCH", "WABA no autorizado.")
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            if (
                not settings.META_PHONE_NUMBER_ID
                or phone_number_id != settings.META_PHONE_NUMBER_ID
            ):
                raise WebhookValidationError(
                    "PHONE_NUMBER_ID_MISMATCH", "Número de Meta no autorizado."
                )
            for item in value.get("statuses") or []:
                provider_message_id = str(item.get("id", ""))
                event_type = str(item.get("status", ""))
                timestamp = str(item.get("timestamp", ""))
                if not provider_message_id or event_type not in {
                    "sent",
                    "delivered",
                    "read",
                    "failed",
                }:
                    continue
                event_key = hashlib.sha256(
                    f"{provider_message_id}|{event_type}|{timestamp}".encode()
                ).hexdigest()
                error_code = ""
                errors = item.get("errors") or []
                if errors:
                    error_code = str(errors[0].get("code", ""))[:100]
                events.append(
                    {
                        "provider_event_id": event_key,
                        "provider_message_id": provider_message_id,
                        "event_type": event_type,
                        "waba_id": waba_id,
                        "phone_number_id": phone_number_id,
                        "error_code": error_code,
                        "kind": "status",
                    }
                )
            for item in value.get("messages") or []:
                inbound_id = str(item.get("id", ""))
                context_id = str((item.get("context") or {}).get("id", ""))
                sender = str(item.get("from", ""))
                message_type = str(item.get("type", ""))
                interactive = item.get("interactive") or {}
                reply_id = ""
                event_type = ""
                text_body = ""
                if message_type == "interactive":
                    interactive_type = str(interactive.get("type", ""))
                    if interactive_type == "button_reply":
                        event_type = "inbound_button"
                        reply_id = str((interactive.get("button_reply") or {}).get("id", ""))
                    elif interactive_type == "list_reply":
                        event_type = "inbound_list"
                        reply_id = str((interactive.get("list_reply") or {}).get("id", ""))
                elif message_type == "text":
                    event_type = "inbound_text"
                    text_body = str((item.get("text") or {}).get("body", ""))
                if not inbound_id or not context_id or not sender or not event_type:
                    continue
                events.append(
                    {
                        "provider_event_id": hashlib.sha256(
                            f"inbound|{inbound_id}".encode()
                        ).hexdigest(),
                        "provider_message_id": context_id,
                        "provider_inbound_id": inbound_id,
                        "event_type": event_type,
                        "waba_id": waba_id,
                        "phone_number_id": phone_number_id,
                        "error_code": "",
                        "kind": "inbound",
                        "sender": sender,
                        "reply_id": reply_id,
                        "text": text_body,
                    }
                )
    return events


def _apply_status(item):
    outbox = WhatsAppOutbox.objects.filter(
        provider_message_id=item["provider_message_id"]
    ).select_related("draft").first()
    if not outbox:
        return
    current_rank = {"pending": 0, "sent": 1, "delivered": 2, "read": 3}
    incoming = item["event_type"]
    should_apply = incoming == "failed" and outbox.state not in {"delivered", "read"}
    if incoming in current_rank:
        should_apply = current_rank[incoming] >= current_rank.get(outbox.state, 0)
    if not should_apply:
        return
    outbox.state = incoming
    fields = ["state", "updated_at"]
    now = timezone.now()
    if incoming == "sent" and not outbox.sent_at:
        outbox.sent_at = now
        fields.append("sent_at")
    elif incoming == "delivered":
        outbox.delivered_at = now
        fields.append("delivered_at")
    elif incoming == "read":
        outbox.read_at = now
        fields.append("read_at")
    elif incoming == "failed":
        outbox.last_error_code = item["error_code"] or "META_DELIVERY_FAILED"
        fields.append("last_error_code")
    outbox.save(update_fields=fields)
    outbox.draft.state = incoming
    outbox.draft.save(update_fields=["state", "updated_at"])


def _apply_inbound(item):
    outbox = (
        WhatsAppOutbox.objects.filter(
            provider_message_id=item["provider_message_id"]
        )
        .select_related("draft")
        .first()
    )
    if not outbox:
        raise WebhookValidationError(
            "INBOUND_CONTEXT_NOT_FOUND", "La respuesta no corresponde a un mensaje de PAMO."
        )
    draft = outbox.draft
    if normalized_phone(item["sender"]) != normalized_phone(draft.recipient_phone):
        raise WebhookValidationError(
            "INBOUND_SENDER_MISMATCH", "La respuesta no corresponde al destinatario del piloto."
        )
    shipment = (
        Shipment.objects.filter(id=draft.source_id)
        .select_related("order", "warehouse")
        .first()
    )
    if not shipment:
        raise WebhookValidationError("INBOUND_SHIPMENT_NOT_FOUND", "Despacho no encontrado.")

    provider_event_id = item["provider_inbound_id"]
    if item["event_type"] in {"inbound_button", "inbound_list"}:
        try:
            action_payload = parse_signed_action(item["reply_id"])
        except InteractivePayloadError as error:
            raise WebhookValidationError(str(error), "Respuesta interactiva inválida.") from error
        if (
            action_payload["s"] != str(shipment.id)
            or action_payload["c"] != draft.contact_reference
        ):
            raise WebhookValidationError(
                "INBOUND_CONTEXT_MISMATCH", "La respuesta no corresponde a este despacho."
            )
        action = action_payload["a"]
        if action in {
            "order_received",
            "request_guide",
            "report_stockout",
            "report_issue",
        }:
            if draft.message_kind != "supplier_order":
                raise WebhookValidationError(
                    "INBOUND_MESSAGE_KIND_MISMATCH", "La acción no corresponde al mensaje citado."
                )
            result = apply_supplier_response(
                shipment_id=shipment.id,
                action=action,
                provider_event_id=provider_event_id,
                sender_phone=item["sender"],
                source="meta_webhook",
                validate_contact=False,
                contact_reference=draft.contact_reference,
            )
            if result["result"] == "applied" and action == "report_issue":
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="novelty_menu",
                )
            elif result["result"] == "applied" and action == "report_stockout":
                message_kind = (
                    "issue_quantity_prompt"
                    if (result.get("openNovelty") or {}).get("detailState")
                    == "awaiting_quantity"
                    else "issue_sku_menu"
                )
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind=message_kind,
                )
            elif (
                result["result"] == "applied"
                and action == "request_guide"
                and result["guideAvailable"]
                and settings.PAMO_WHATSAPP_GUIDE_AUTO_SEND_ENABLED
            ):
                delivery = prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="guide_delivery",
                )
                if delivery["outbox"].state in {"sent", "delivered", "read"}:
                    shipment.guide_delivery_state = "sent"
                    shipment.save(update_fields=["guide_delivery_state", "updated_at"])
            return
        if action.startswith("issue_item:"):
            if draft.message_kind != "issue_sku_menu":
                raise WebhookValidationError(
                    "INBOUND_MESSAGE_KIND_MISMATCH",
                    "La referencia no corresponde al mensaje citado.",
                )
            result = apply_supplier_issue_item(
                shipment_id=shipment.id,
                shipment_item_id=action.split(":", 1)[1],
                provider_event_id=provider_event_id,
                sender_phone=item["sender"],
                source="meta_webhook",
                validate_contact=False,
            )
            if (result.get("openNovelty") or {}).get("detailState") == "awaiting_quantity":
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="issue_quantity_prompt",
                )
            return
        if action.startswith("supplier_"):
            if draft.message_kind != "novelty_menu":
                raise WebhookValidationError(
                    "INBOUND_MESSAGE_KIND_MISMATCH", "La novedad no corresponde al mensaje citado."
                )
            result = apply_supplier_novelty_category(
                shipment_id=shipment.id,
                category=action,
                provider_event_id=provider_event_id,
                sender_phone=item["sender"],
                source="meta_webhook",
                validate_contact=False,
            )
            detail_state = (result.get("openNovelty") or {}).get("detailState")
            if detail_state == "awaiting_item":
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="issue_sku_menu",
                )
            elif detail_state == "awaiting_detail":
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="novelty_prompt",
                )
            elif detail_state == "complete":
                prepare_and_dispatch_workflow(
                    shipment=shipment,
                    contact_reference=draft.contact_reference,
                    message_kind="novelty_confirmation",
                )
            return
        raise WebhookValidationError("INBOUND_ACTION_UNSUPPORTED", "Acción no soportada.")

    if draft.message_kind == "issue_quantity_prompt":
        result = apply_supplier_issue_quantity(
            shipment_id=shipment.id,
            quantity=item["text"],
            provider_event_id=provider_event_id,
            sender_phone=item["sender"],
            source="meta_webhook",
            validate_contact=False,
        )
        if (result.get("openNovelty") or {}).get("detailState") == "complete":
            prepare_and_dispatch_workflow(
                shipment=shipment,
                contact_reference=draft.contact_reference,
                message_kind="novelty_confirmation",
            )
        return
    if draft.message_kind != "novelty_prompt":
        raise WebhookValidationError(
            "INBOUND_TEXT_CONTEXT_INVALID",
            "El texto solo se acepta como respuesta a la solicitud de detalle.",
        )
    result = apply_supplier_novelty_detail(
        shipment_id=shipment.id,
        detail=item["text"],
        provider_event_id=provider_event_id,
        sender_phone=item["sender"],
        source="meta_webhook",
        validate_contact=False,
    )
    if (result.get("openNovelty") or {}).get("detailState") == "complete":
        prepare_and_dispatch_workflow(
            shipment=shipment,
            contact_reference=draft.contact_reference,
            message_kind="novelty_confirmation",
        )


@transaction.atomic
def process_webhook(*, raw_body, signature_header):
    if not verify_signature(raw_body, signature_header):
        raise WebhookValidationError("WEBHOOK_SIGNATURE_INVALID", "Firma inválida.")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebhookValidationError("WEBHOOK_JSON_INVALID", "JSON inválido.") from error
    payload_digest = hashlib.sha256(raw_body).hexdigest()
    processed = 0
    duplicates = 0
    rejected = 0
    for item in _extract_events(payload):
        event, created = WhatsAppWebhookEvent.objects.get_or_create(
            provider_event_id=item["provider_event_id"],
            defaults={
                "provider_message_id": item["provider_message_id"],
                "event_type": item["event_type"],
                "waba_id": item["waba_id"],
                "phone_number_id": item["phone_number_id"],
                "error_code": item["error_code"],
                "payload_digest": payload_digest,
                "signature_valid": True,
            },
        )
        if not created:
            WhatsAppWebhookEvent.objects.filter(id=event.id).update(
                duplicate_count=F("duplicate_count") + 1
            )
            duplicates += 1
            continue
        try:
            if item["kind"] == "status":
                _apply_status(item)
            else:
                _apply_inbound(item)
        except (WebhookValidationError, SupplierResponseError, DraftValidationError) as error:
            event.error_code = getattr(error, "code", "INBOUND_PROCESSING_FAILED")
            event.processed = False
            event.processed_at = timezone.now()
            event.save(update_fields=["error_code", "processed", "processed_at"])
            rejected += 1
            continue
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at"])
        processed += 1
    return {"processed": processed, "duplicates": duplicates, "rejected": rejected}
