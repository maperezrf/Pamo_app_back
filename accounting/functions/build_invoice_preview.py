from decimal import Decimal

from rest_framework.exceptions import ValidationError

from config.constants import EXTERNAL_WRITES_ENABLED, SIIGO_INVOICE_WRITES_ENABLED

from .ensure_invoice_lines import ensure_invoice_lines


def build_invoice_preview(remittance):
    invoice_lines = ensure_invoice_lines(remittance)
    missing = [
        invoice_line.remittance_line.line_number
        for invoice_line in invoice_lines
        if not invoice_line.siigo_sku or invoice_line.invoice_unit_price is None
    ]
    if missing:
        raise ValidationError({"lines": f"Falta codificar o valorar las líneas: {missing}."})

    items = []
    subtotal = Decimal("0")
    for invoice_line in invoice_lines:
        line = invoice_line.remittance_line
        line_total = line.quantity * invoice_line.invoice_unit_price
        subtotal += line_total
        items.append({
            "line_number": line.line_number,
            "sku": invoice_line.siigo_sku,
            "description": invoice_line.invoice_description or line.original_description,
            "quantity": line.quantity,
            "unit_price": invoice_line.invoice_unit_price,
            "total": line_total,
        })
    return {
        "remittance_id": remittance.id,
        "remittance_number": remittance.number,
        "customer": {"siigo_id": remittance.customer.siigo_id, "nit": remittance.customer.nit, "name": remittance.customer.name},
        "payment_method": "CREDIT",
        "payment_days": 15,
        "items": items,
        "subtotal": subtotal,
        "external_writes_enabled": EXTERNAL_WRITES_ENABLED and SIIGO_INVOICE_WRITES_ENABLED,
    }
