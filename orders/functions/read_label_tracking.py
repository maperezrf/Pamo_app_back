import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Formatos vistos, en orden:
# - Rótulo de TCC: "REMESA <número>" (9 dígitos, p. ej. 473402021). Se
#   tolera texto corto entre la palabra y el número.
# - Rótulo de Sodimac "Paquetería - Home Delivery": "Guía: <número>" (el
#   número de la OC, visto el 2026-10-11).
PATTERNS = (
    re.compile(r"REMESA\D{0,30}?(\d{6,12})", re.IGNORECASE),
    re.compile(r"GU[IÍ]A\s*:\s*(\d{6,12})", re.IGNORECASE),
)


def read_label_tracking(label_pdf):
    """Número de guía leído del texto del rótulo (ver `PATTERNS`), o "" si
    no se encuentra. El RPA de Sodimac no lo envía (`numero_guia` llega
    vacío), pero el portal lo imprime en el rótulo."""
    try:
        reader = PdfReader(io.BytesIO(label_pdf))
        text = " ".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, OSError):
        return ""
    for pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""
