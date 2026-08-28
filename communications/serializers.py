def masked_phone(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return f"••••{digits[-4:]}" if digits else "Sin teléfono"


def draft_payload(draft):
    outbox = getattr(draft, "outbox", None)
    return {
        "id": str(draft.id),
        "source": {
            "module": draft.source_module,
            "type": draft.source_type,
            "id": draft.source_id,
            "order": draft.order_visible_id,
        },
        "messageKind": draft.message_kind,
        "warehouse": draft.warehouse_reference,
        "recipient": {
            "reference": draft.contact_reference,
            "name": draft.recipient_name,
            "phoneMasked": masked_phone(draft.recipient_phone),
        },
        "body": draft.rendered_body,
        "interactive": draft.interactive_payload or None,
        "autoPrepared": draft.auto_prepared,
        "document": {
            "available": bool(draft.document_source_id),
            "name": draft.document_name or None,
            "sha256": draft.document_sha256 or None,
        },
        "state": draft.state,
        "approvedBy": draft.approved_by or None,
        "approvedAt": draft.approved_at.isoformat() if draft.approved_at else None,
        "outbox": outbox_payload(outbox) if outbox else None,
        "createdAt": draft.created_at.isoformat(),
    }


def outbox_payload(outbox):
    return {
        "id": str(outbox.id),
        "provider": outbox.provider,
        "state": outbox.state,
        "attemptCount": outbox.attempt_count,
        "lastErrorCode": outbox.last_error_code or None,
        "hasProviderMessageId": bool(outbox.provider_message_id),
        "hasMediaId": bool(outbox.media_id),
        "sentAt": outbox.sent_at.isoformat() if outbox.sent_at else None,
        "deliveredAt": outbox.delivered_at.isoformat() if outbox.delivered_at else None,
        "readAt": outbox.read_at.isoformat() if outbox.read_at else None,
    }
