from ..client import WhatsAppClient


class SendDocumentMessageError(ValueError):
    pass


def send_document_message(to, *, media_id=None, link=None, caption="", filename=""):
    """Envía un PDF como mensaje independiente (sin plantilla). Requiere
    exactamente uno de `media_id` (subido antes con upload_document) o
    `link` (URL pública accesible por Meta).

    Requiere ventana de servicio de 24h activa, igual que un texto libre
    -- salvo que el PDF vaya dentro de una plantilla aprobada (ver
    send_template_message, que adjunta el documento en el header).

    Devuelve {"message_id": str, "raw": dict}.
    """
    if bool(media_id) == bool(link):
        raise SendDocumentMessageError("WHATSAPP_DOCUMENT_NEEDS_MEDIA_ID_OR_LINK")
    document = {"id": media_id} if media_id else {"link": link}
    if caption:
        document["caption"] = caption
    if filename:
        document["filename"] = filename
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": document,
    }
    response = WhatsAppClient().send_message(payload)
    return {"message_id": response["messages"][0]["id"], "raw": response}
