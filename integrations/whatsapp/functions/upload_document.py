from ..client import WhatsAppClient


class UploadDocumentError(ValueError):
    pass


def upload_document(content, filename):
    """Sube un PDF a Meta y devuelve el `media_id` para usarlo después
    en send_document_message o send_template_message.

    `content`: bytes del archivo PDF.
    """
    if not isinstance(content, bytes) or not content:
        raise UploadDocumentError("WHATSAPP_DOCUMENT_CONTENT_REQUIRED")
    response = WhatsAppClient().upload_media(content, filename, "application/pdf")
    media_id = response.get("id")
    if not media_id:
        raise UploadDocumentError("WHATSAPP_MEDIA_ID_MISSING")
    return media_id
