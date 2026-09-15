import re

from ..client import WhatsAppClient

_TEMPLATE_NAME_RE = re.compile(r"[a-z0-9_]{1,512}")


class SendTemplateMessageError(ValueError):
    pass


def send_template_message(to, name, *, language="es_CO", body_params=(), document_media_id=""):
    """Envía una plantilla aprobada por Meta. `body_params` son los
    valores en el orden exacto de las variables {{1}}, {{2}}... que
    defina la plantilla aprobada -- esta función no sabe cuál plantilla
    existe ni cuántas variables tiene, eso lo decide quien llama.

    Los botones de una plantilla NO se arman acá: ya quedaron fijos al
    aprobarla en Meta. Esta función solo elige el contenido variable
    (header de documento y/o cuerpo).

    Devuelve {"message_id": str, "raw": dict}.
    """
    if not _TEMPLATE_NAME_RE.fullmatch(name or ""):
        raise SendTemplateMessageError("WHATSAPP_TEMPLATE_NAME_INVALID")
    components = []
    if document_media_id:
        components.append({
            "type": "header",
            "parameters": [{"type": "document", "document": {"id": document_media_id}}],
        })
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(value)} for value in body_params],
        })
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": language},
            "components": components,
        },
    }
    response = WhatsAppClient().send_message(payload)
    return {"message_id": response["messages"][0]["id"], "raw": response}
