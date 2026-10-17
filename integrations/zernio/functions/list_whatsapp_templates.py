from config.constants import ZERNIO_WHATSAPP_ACCOUNT_ID

from ..client import ZernioClient


def list_whatsapp_templates():
    """Plantillas de la cuenta de WhatsApp Business conectada en Zernio
    (Zernio las lee de Meta), para saber cuál está aprobada y qué
    variables pide. Solo lectura.

    Devuelve [{"name", "language", "status", "category", "body",
    "body_variables", "header_format"}]: `body_variables` es cuántas
    variables {{n}} tiene el cuerpo; `header_format` es el tipo de
    encabezado ("DOCUMENT", "TEXT"...) o "" si no tiene.
    """
    data = ZernioClient().get("whatsapp/templates", params={"accountId": ZERNIO_WHATSAPP_ACCOUNT_ID})
    templates = []
    for row in data.get("templates") or []:
        components = {component.get("type"): component for component in row.get("components") or []}
        body = (components.get("BODY") or {}).get("text") or ""
        templates.append({
            "name": row.get("name") or "",
            "language": row.get("language") or "",
            "status": row.get("status") or "",
            "category": row.get("category") or "",
            "body": body,
            "body_variables": body.count("{{"),
            "header_format": (components.get("HEADER") or {}).get("format") or "",
        })
    return templates
