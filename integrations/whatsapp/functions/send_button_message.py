from ..client import WhatsAppClient

MAX_BUTTONS = 3
MAX_BUTTON_TITLE_LENGTH = 20


class SendButtonMessageError(ValueError):
    pass


def send_button_message(to, body, buttons):
    """Envía texto libre con hasta 3 botones de respuesta rápida, sin
    necesidad de una plantilla aprobada. Requiere ventana de servicio de
    24h activa, igual que un texto libre.

    `buttons`: lista de {"id": str, "title": str} -- `id` es lo que
    vuelve en la respuesta del contacto para identificar qué botón tocó.

    Devuelve {"message_id": str, "raw": dict}.
    """
    if not buttons or len(buttons) > MAX_BUTTONS:
        raise SendButtonMessageError("WHATSAPP_BUTTON_COUNT_INVALID")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": str(button["id"]),
                            "title": str(button["title"])[:MAX_BUTTON_TITLE_LENGTH],
                        },
                    }
                    for button in buttons
                ]
            },
        },
    }
    response = WhatsAppClient().send_message(payload)
    return {"message_id": response["messages"][0]["id"], "raw": response}
