import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pedidos.models import ShipmentDocument

from .models import WhatsAppAttempt, WhatsAppDraft, WhatsAppOutbox
from .providers import WhatsAppProviderError, provider_client


class InvalidDraftState(Exception):
    pass


@transaction.atomic
def approve_draft(*, draft_id, actor):
    draft = WhatsAppDraft.objects.select_for_update().filter(id=draft_id).first()
    if not draft:
        return None
    if draft.state in {"approved", "queued", "sent", "delivered", "read"}:
        return draft
    if draft.state not in {"draft", "failed"}:
        raise InvalidDraftState("El borrador no se puede aprobar en su estado actual.")
    draft.state = "approved"
    draft.approved_by = actor
    draft.approved_at = timezone.now()
    draft.save(update_fields=["state", "approved_by", "approved_at", "updated_at"])
    return draft


@transaction.atomic
def enqueue_draft(*, draft_id):
    draft = WhatsAppDraft.objects.select_for_update().filter(id=draft_id).first()
    if not draft:
        return None, False
    if draft.state not in {"approved", "queued", "sent", "delivered", "read"}:
        raise InvalidDraftState("Se requiere aprobación humana antes de encolar.")
    outbox, created = WhatsAppOutbox.objects.get_or_create(
        draft=draft,
        defaults={
            "provider": str(settings.PAMO_WHATSAPP_PROVIDER or "mock").lower(),
            "idempotency_key": draft.idempotency_key,
        },
    )
    if draft.state == "approved":
        draft.state = "queued"
        draft.save(update_fields=["state", "updated_at"])
    return outbox, created


def _request_digest(outbox, media_id):
    return hashlib.sha256(
        (
            f"{outbox.idempotency_key}|"
            f"{hashlib.sha256(outbox.draft.rendered_body.encode()).hexdigest()}|"
            f"{media_id}|{json.dumps(outbox.draft.interactive_payload or {}, sort_keys=True)}"
        ).encode()
    ).hexdigest()


def dispatch_outbox(*, outbox_id, client=None):
    error_to_raise = None
    dispatched = False
    with transaction.atomic():
        outbox = (
            WhatsAppOutbox.objects.select_for_update()
            .select_related("draft")
            .filter(id=outbox_id)
            .first()
        )
        if not outbox:
            return None, False
        if outbox.state in {"sent", "delivered", "read"}:
            return outbox, False
        if outbox.last_error_code in {"META_TOKEN_INVALID", "META_CONNECTION_BLOCKED"}:
            error_to_raise = WhatsAppProviderError(
                "META_CONNECTION_BLOCKED",
                "La conexión con Meta está bloqueada hasta validar un token nuevo.",
            )
        media_id = outbox.media_id
        if not error_to_raise:
            try:
                provider = client or provider_client(
                    outbox.provider, recipient_phone=outbox.draft.recipient_phone
                )
                if outbox.draft.document_source_id and not media_id:
                    document = ShipmentDocument.objects.filter(
                        shipment_id=outbox.draft.document_source_id
                    ).first()
                    if not document:
                        raise WhatsAppProviderError(
                            "DOCUMENT_NOT_FOUND", "La guía aprobada ya no está disponible."
                        )
                    with document.file.open("rb") as file_object:
                        uploaded = provider.upload_media(
                            file_object=file_object,
                            mime_type=document.mime_type,
                            filename=document.original_name,
                        )
                    media_id = uploaded["id"]
                result = provider.send_message(
                    recipient_phone=outbox.draft.recipient_phone,
                    body=outbox.draft.rendered_body,
                    media_id=media_id,
                    filename=outbox.draft.document_name,
                    interactive_payload=outbox.draft.interactive_payload,
                )
            except WhatsAppProviderError as error:
                outbox.attempt_count += 1
                outbox.state = "failed"
                outbox.last_error_code = error.code
                outbox.save(
                    update_fields=["attempt_count", "state", "last_error_code", "updated_at"]
                )
                WhatsAppAttempt.objects.create(
                    outbox=outbox,
                    sequence=outbox.attempt_count,
                    outcome="failed",
                    error_code=error.code,
                    request_digest=_request_digest(outbox, media_id),
                )
                outbox.draft.state = "failed"
                outbox.draft.save(update_fields=["state", "updated_at"])
                error_to_raise = error
            else:
                outbox.attempt_count += 1
                outbox.state = "sent"
                outbox.media_id = media_id
                outbox.provider_message_id = result["id"]
                outbox.last_error_code = ""
                outbox.sent_at = timezone.now()
                outbox.save(
                    update_fields=[
                        "attempt_count",
                        "state",
                        "media_id",
                        "provider_message_id",
                        "last_error_code",
                        "sent_at",
                        "updated_at",
                    ]
                )
                WhatsAppAttempt.objects.create(
                    outbox=outbox,
                    sequence=outbox.attempt_count,
                    outcome="sent",
                    request_digest=_request_digest(outbox, media_id),
                    provider_reference=result["id"],
                )
                outbox.draft.state = "sent"
                outbox.draft.save(update_fields=["state", "updated_at"])
                dispatched = True
    if error_to_raise:
        raise error_to_raise
    return outbox, dispatched
