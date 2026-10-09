from ..client import WhatsAppClient


def list_message_templates():
    """Plantillas de la cuenta de WhatsApp Business (Meta), para saber cuál
    está aprobada y qué variables pide. Solo lectura.

    Devuelve [{"name", "language", "status", "category", "body",
    "body_variables", "header_format"}]: `body_variables` es cuántas
    variables {{n}} tiene el cuerpo; `header_format` es el tipo de
    encabezado ("DOCUMENT", "TEXT"...) o "" si no tiene.
    """
    data = WhatsAppClient().get_business(
        "message_templates", params={"fields": "name,language,status,category,components", "limit": 100}
    )
    templates = []
    for row in data.get("data") or []:
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
