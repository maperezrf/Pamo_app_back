import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# El rótulo de TCC trae "REMESA <número>" (9 dígitos en las guías vistas,
# p. ej. 473402021). Se tolera texto corto entre la palabra y el número.
REMESA_PATTERN = re.compile(r"REMESA\D{0,30}?(\d{6,12})", re.IGNORECASE)


def read_label_tracking(label_pdf):
    """Número de guía (remesa) leído del texto del rótulo, o "" si no se
    encuentra. El RPA de Sodimac no lo envía (`numero_guia` llega vacío),
    pero el portal lo imprime en el rótulo."""
    try:
        reader = PdfReader(io.BytesIO(label_pdf))
        text = " ".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, OSError):
        return ""
    match = REMESA_PATTERN.search(text)
    return match.group(1) if match else ""
