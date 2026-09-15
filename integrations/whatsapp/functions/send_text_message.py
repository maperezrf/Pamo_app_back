from ..client import WhatsAppClient


def send_text_message(to, body):
    """Envía un mensaje de texto libre. Solo funciona si el contacto le
    escribió a la línea en las últimas 24 horas (ventana de servicio de
    Meta) -- Meta rechaza el envío si no.

    `to`: teléfono en formato E.164 sin "+" (ej. "573001234567").
    `body`: texto del mensaje.

    Devuelve {"message_id": str, "raw": dict}.
    """
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    response = WhatsAppClient().send_message(payload)
    return {"message_id": response["messages"][0]["id"], "raw": response}
