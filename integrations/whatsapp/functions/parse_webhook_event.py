from config.constants import WHATSAPP_BUSINESS_ACCOUNT_ID, WHATSAPP_PHONE_NUMBER_ID


class WhatsAppWebhookError(ValueError):
    pass


def parse_webhook_event(payload):
    """Normaliza el payload de un webhook de Meta ya verificado (ver
    WhatsAppClient.verify_signature) en una lista de eventos planos.
    Cada evento es una actualización de estado de un mensaje que
    enviamos ("status") o un mensaje entrante del contacto ("inbound").

    No persiste nada ni decide qué hacer con el evento -- eso es lógica
    de negocio de quien consuma esta función.
    """
    if payload.get("object") != "whatsapp_business_account":
        raise WhatsAppWebhookError("WHATSAPP_WEBHOOK_OBJECT_INVALID")
    events = []
    for entry in payload.get("entry") or []:
        if str(entry.get("id", "")) != str(WHATSAPP_BUSINESS_ACCOUNT_ID):
            raise WhatsAppWebhookError("WHATSAPP_WABA_MISMATCH")
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            if str(metadata.get("phone_number_id", "")) != str(WHATSAPP_PHONE_NUMBER_ID):
                raise WhatsAppWebhookError("WHATSAPP_PHONE_NUMBER_MISMATCH")
            events.extend(_status_events(value))
            events.extend(_inbound_events(value))
    return events


def _status_events(value):
    events = []
    for status in value.get("statuses") or []:
        events.append({
            "type": "status",
            "message_id": str(status.get("id", "")),
            "status": str(status.get("status", "")).upper(),
            "timestamp": status.get("timestamp"),
        })
    return events


def _inbound_events(value):
    events = []
    for message in value.get("messages") or []:
        button = message.get("button") or {}
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        events.append({
            "type": "inbound",
            "message_id": str(message.get("id", "")),
            "from": str(message.get("from", "")),
            "context_message_id": str((message.get("context") or {}).get("id", "")),
            "text": (message.get("text") or {}).get("body", ""),
            "button_payload": str(button.get("payload") or reply.get("id") or ""),
            "timestamp": message.get("timestamp"),
        })
    return events
