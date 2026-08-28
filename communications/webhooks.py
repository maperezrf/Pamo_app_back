import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import WhatsAppWebhookEvent, WhatsAppOutbox


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


def _extract_status_events(payload):
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
                    }
                )
    return events


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
    for item in _extract_status_events(payload):
        event, created = WhatsAppWebhookEvent.objects.get_or_create(
            provider_event_id=item["provider_event_id"],
            defaults={
                **item,
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
        outbox = WhatsAppOutbox.objects.filter(
            provider_message_id=item["provider_message_id"]
        ).select_related("draft").first()
        if outbox:
            current_rank = {"pending": 0, "sent": 1, "delivered": 2, "read": 3}
            incoming = item["event_type"]
            should_apply = incoming == "failed" and outbox.state not in {"delivered", "read"}
            if incoming in current_rank:
                should_apply = current_rank[incoming] >= current_rank.get(outbox.state, 0)
            if should_apply:
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
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at"])
        processed += 1
    return {"processed": processed, "duplicates": duplicates}

