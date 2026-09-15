from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from pypdf import PdfReader


MAX_SUPPLIER_INVOICE_BYTES = 12_000_000
MAX_SUPPLIER_INVOICE_PDF_PAGES = 25
MAX_SUPPLIER_INVOICE_TEXT_CHARS = 200_000

MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}


class SupplierInvoiceError(ValueError):
    def __init__(self, message, *, status_code=400, code="SUPPLIER_INVOICE_INVALID"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ValidatedSupplierInvoice:
    body: bytes
    mime_type: str
    extension: str
    sha256: str
    original_name: str


def _valid_xlsx(body):
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = set(archive.namelist())
            return "[Content_Types].xml" in names and "xl/workbook.xml" in names
    except zipfile.BadZipFile:
        return False


def validate_supplier_invoice(uploaded_file):
    original_name = Path(uploaded_file.name or "factura-proveedor").name[:180]
    extension = Path(original_name).suffix.lower()
    expected_mime = MIME_BY_EXTENSION.get(extension)
    declared_mime = (getattr(uploaded_file, "content_type", "") or "").lower().split(";", 1)[0]
    if not expected_mime:
        raise SupplierInvoiceError(
            "La factura debe ser PDF, JPG, PNG, WebP o Excel (XLSX/XLS).",
            status_code=415,
            code="UNSUPPORTED_FILE_TYPE",
        )
    if declared_mime and declared_mime not in {expected_mime, "application/octet-stream"}:
        raise SupplierInvoiceError(
            "La extensión y el tipo declarado del archivo no coinciden.",
            status_code=415,
            code="MIME_MISMATCH",
        )

    body = uploaded_file.read(MAX_SUPPLIER_INVOICE_BYTES + 1)
    if not body or len(body) > MAX_SUPPLIER_INVOICE_BYTES:
        raise SupplierInvoiceError(
            "La factura está vacía o supera el máximo de 12 MB.",
            status_code=413,
            code="FILE_SIZE_INVALID",
        )

    valid_magic = {
        ".pdf": body.startswith(b"%PDF"),
        ".png": body.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": body.startswith(b"\xff\xd8\xff"),
        ".jpeg": body.startswith(b"\xff\xd8\xff"),
        ".webp": body.startswith(b"RIFF") and body[8:12] == b"WEBP",
        ".xlsx": body.startswith(b"PK\x03\x04") and _valid_xlsx(body),
        ".xls": body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    }[extension]
    if not valid_magic:
        raise SupplierInvoiceError(
            "El contenido real no corresponde al tipo de factura seleccionado.",
            status_code=415,
            code="MIME_MISMATCH",
        )

    return ValidatedSupplierInvoice(
        body=body,
        mime_type=expected_mime,
        extension=extension.lstrip("."),
        sha256=hashlib.sha256(body).hexdigest(),
        original_name=original_name,
    )


def _decimal(value, *, minimum=Decimal("0")):
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SupplierInvoiceError("La lectura devolvió un valor numérico inválido.", status_code=502, code="AI_RESPONSE_INVALID") from error
    if not number.is_finite() or number < minimum:
        raise SupplierInvoiceError("La lectura devolvió un valor numérico inválido.", status_code=502, code="AI_RESPONSE_INVALID")
    return number


def _json_decimal(value, places="0.01"):
    number = _decimal(value)
    return None if number is None else float(number.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def normalize_supplier_invoice_payload(payload):
    source_lines = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(source_lines, list):
        raise SupplierInvoiceError("La lectura no devolvió productos utilizables.", status_code=502, code="AI_RESPONSE_INVALID")

    lines = []
    for source in source_lines[:200]:
        if not isinstance(source, dict):
            continue
        quantity = _decimal(source.get("quantity"), minimum=Decimal("0.001"))
        description = " ".join(str(source.get("description") or "").split()).upper()[:500]
        if quantity is None or quantity <= 0 or not description:
            continue
        total = _decimal(source.get("totalPrice"))
        unit = _decimal(source.get("unitPrice"))
        if unit is None and total is not None:
            unit = (total / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        confidence = source.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not math.isfinite(confidence):
            confidence = None
        lines.append({
            "supplier_sku": " ".join(str(source.get("sku") or "").split()).upper()[:120],
            "quantity": float(quantity),
            "description": description,
            "supplier_unit_cost": _json_decimal(unit),
            "supplier_line_total": _json_decimal(total),
            "supplier_discount_percent": _json_decimal(source.get("discountPercent"), "0.0001"),
            "supplier_discount_value": _json_decimal(source.get("discountValue")),
            "confidence": max(0, min(1, confidence)) if confidence is not None else None,
            "warning": (" ".join(str(source.get("warning") or "").split())[:500] or None),
        })

    if not lines:
        raise SupplierInvoiceError(
            "No se identificaron productos utilizables. Puedes continuar en captura manual.",
            status_code=422,
            code="NO_USABLE_LINES",
        )
    return {
        "lines": lines,
        "global_discount_percent": _json_decimal(payload.get("globalDiscountPercent"), "0.0001"),
        "global_discount_value": _json_decimal(payload.get("globalDiscountValue")),
        "other_charges": _json_decimal(payload.get("otherCharges")),
        "freight_cost": _json_decimal(payload.get("freightCost")),
        "warnings": [" ".join(str(item).split())[:500] for item in (payload.get("warnings") or []) if str(item).strip()][:20],
    }


def _localized_decimal(value):
    """Convierte importes con separadores ES/US sin adivinar valores ambiguos."""
    text = str(value or "").strip().replace("$", "").replace(" ", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None

    comma = text.rfind(",")
    point = text.rfind(".")
    if comma >= 0 and point >= 0:
        decimal_separator = "," if comma > point else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif comma >= 0:
        tail = len(text) - comma - 1
        text = text.replace(",", ".") if tail in {1, 2} else text.replace(",", "")
    elif point >= 0:
        tail = len(text) - point - 1
        if tail not in {1, 2}:
            text = text.replace(".", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _normalized_header(value):
    return (
        str(value or "")
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )


def _header_indexes(cells):
    indexes = {}
    for index, cell in enumerate(cells):
        value = _normalized_header(cell)
        if any(token in value for token in ("cantidad", "cant.", "cant ", "qty")):
            indexes["quantity"] = index
        elif any(token in value for token in ("sku", "codigo", "referencia", "ref.")):
            indexes["sku"] = index
        elif "total" in value:
            indexes["total"] = index
        elif any(token in value for token in ("unitario", "vr. unit", "valor unit", "precio unit")):
            indexes["unit"] = index
        elif any(token in value for token in ("descripcion", "nombre producto", "producto", "detalle")):
            indexes["description"] = index
    return indexes


def extract_supplier_invoice_pdf_text(body):
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
        if reader.is_encrypted or len(reader.pages) > MAX_SUPPLIER_INVOICE_PDF_PAGES:
            return ""
        pages = []
        total_chars = 0
        for page in reader.pages:
            page_text = page.extract_text(extraction_mode="layout") or ""
            total_chars += len(page_text)
            if total_chars > MAX_SUPPLIER_INVOICE_TEXT_CHARS:
                return ""
            pages.append(page_text)
        return "\n".join(pages).strip()
    except Exception:  # El PDF puede ser válido, pero carecer de una capa de texto utilizable.
        return ""


def parse_supplier_invoice_text(text):
    """Extrae tablas digitales claras; ante ambigüedad deja el caso para IA/manual."""
    rows = [line.rstrip() for line in str(text or "").splitlines()]
    header_index = None
    header_cells = None
    indexes = None
    for index, row in enumerate(rows):
        cells = [cell.strip() for cell in re.split(r"\s{2,}", row.strip()) if cell.strip()]
        candidate = _header_indexes(cells)
        if {"quantity", "description", "total"}.issubset(candidate):
            header_index, header_cells, indexes = index, cells, candidate
            break
    if header_index is None:
        return None

    stop_labels = (
        "descuento", "subtotal", "iva ", "total", "resumen impuestos",
        "forma de pago", "retencion", "retefuente", "reteica",
    )
    raw_lines = []
    skipped_rows = 0
    for row in rows[header_index + 1:]:
        stripped = row.strip()
        if not stripped:
            continue
        normalized = _normalized_header(stripped)
        if any(normalized.startswith(label) for label in stop_labels):
            break
        cells = [cell.strip() for cell in re.split(r"\s{2,}", stripped) if cell.strip()]
        if len(cells) != len(header_cells):
            if raw_lines and len(cells) == 1 and not re.search(r"\d", cells[0]):
                raw_lines[-1]["description"] = f"{raw_lines[-1]['description']} {cells[0]}".strip()
            else:
                skipped_rows += 1
            continue

        quantity = _localized_decimal(cells[indexes["quantity"]])
        total = _localized_decimal(cells[indexes["total"]])
        description = cells[indexes["description"]].strip()
        if not quantity or quantity <= 0 or total is None or not description:
            skipped_rows += 1
            continue
        unit = _localized_decimal(cells[indexes["unit"]]) if "unit" in indexes else None
        sku = cells[indexes["sku"]].strip() if "sku" in indexes else None
        raw_lines.append({
            "sku": sku,
            "quantity": str(quantity),
            "description": description,
            "unitPrice": str(unit) if unit is not None else None,
            "totalPrice": str(total),
            "discountPercent": None,
            "discountValue": None,
            "confidence": 0.9,
            "warning": "REVISA ESTE RENGLÓN: lectura local del texto del PDF.",
        })

    if not raw_lines:
        return None

    summary = "\n".join(rows[header_index + 1:])
    global_discount_value = None
    discount_match = re.search(r"(?im)^\s*descuentos?\s{2,}([^\n]+)$", summary)
    if discount_match:
        global_discount_value = _localized_decimal(discount_match.group(1))

    vat_match = re.search(r"(?i)IVA\s*([0-9]+(?:[.,][0-9]+)?)\s*%", summary)
    warnings = [
        "Lectura local de PDF digital: verifica productos, cantidades, costos y descuentos antes de guardar.",
    ]
    if vat_match:
        warnings.append(f"Se identificó IVA {vat_match.group(1).replace(',', '.')}% en el resumen de la factura.")
    if skipped_rows:
        warnings.append(f"{skipped_rows} fila(s) ambigua(s) no se agregaron al borrador.")

    return normalize_supplier_invoice_payload({
        "lines": raw_lines,
        "globalDiscountPercent": None,
        "globalDiscountValue": str(global_discount_value) if global_discount_value is not None else None,
        "otherCharges": None,
        "freightCost": None,
        "warnings": warnings,
    })


def parse_supplier_invoice_pdf(body):
    text = extract_supplier_invoice_pdf_text(body)
    return parse_supplier_invoice_text(text) if text else None
