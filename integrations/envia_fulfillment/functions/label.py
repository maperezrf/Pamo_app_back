import base64

MAX_LABEL_BYTES = 12 * 1024 * 1024


def label_payload(pdf_bytes, name):
    """Archivo de guía en el formato de Envía: {"name", "type", "raw"}.

    POR VERIFICAR EN SANDBOX: la tabla de la colección oficial dice que
    `raw` es "Base64 file data", pero su ejemplo manda un data URL
    (`data:<tipo>;base64,...`). Se usa el data URL del ejemplo.
    """
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("La guía debe ser un PDF")
    if len(pdf_bytes) > MAX_LABEL_BYTES:
        raise ValueError("La guía supera el tamaño máximo")
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    return {"name": name, "type": "application/pdf", "raw": f"data:application/pdf;base64,{encoded}"}
