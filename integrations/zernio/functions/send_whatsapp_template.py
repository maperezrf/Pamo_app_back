import re

from config.constants import ZERNIO_WHATSAPP_ACCOUNT_ID

from ..client import ZernioClient

_TEMPLATE_NAME_RE = re.compile(r"[a-z0-9_]{1,512}")


class SendWhatsAppTemplateError(ValueError):
    pass


def send_whatsapp_template(to, name, *, language="es_CO", body_params=(), document_url="", document_filename=""):
    """Envía una plantilla de WhatsApp aprobada a un número, desde la cuenta
    de WhatsApp conectada en Zernio (`POST /inbox/conversations`). Si ya
    hay conversación con ese número, el mensaje entra en ella.

    `to`: teléfono en formato E.164 sin "+" (ej. "573001234567").
    `body_params`: valores en el orden exacto de las variables {{1}},
    {{2}}... de la plantilla aprobada -- esta función no sabe cuál plantilla
    existe ni cuántas variables tiene, eso lo decide quien llama. Zernio
    rechaza (INVALID_TEMPLATE_PARAMS) si no cubren todas.
    `document_url`: URL pública del PDF del encabezado (ver upload_media).
    Sin ella, una plantilla con encabezado de documento se envía con el
    archivo de muestra con que se aprobó.

    Devuelve {"message_id": str, "raw": dict}.
    """
    if not _TEMPLATE_NAME_RE.fullmatch(name or ""):
        raise SendWhatsAppTemplateError("WHATSAPP_TEMPLATE_NAME_INVALID")
    payload = {
        "accountId": ZERNIO_WHATSAPP_ACCOUNT_ID,
        "participantId": to,
        "templateName": name,
        "templateLanguage": language,
        "templateParams": [str(value) for value in body_params],
    }
    if document_url:
        payload["headerMedia"] = {"type": "document", "link": document_url}
        if document_filename:
            payload["headerMedia"]["filename"] = document_filename
    response = ZernioClient().post_json("inbox/conversations", payload)
    return {"message_id": str((response.get("data") or {}).get("messageId") or ""), "raw": response}
