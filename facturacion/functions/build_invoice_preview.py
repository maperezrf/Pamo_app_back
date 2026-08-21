from decimal import Decimal

from rest_framework.exceptions import ValidationError


def build_invoice_preview(remittance):
    lines = list(remittance.lines.all())
    missing = [line.line_number for line in lines if not line.siigo_sku or line.invoice_unit_price is None]
    if missing:
        raise ValidationError({"lines": f"Falta codificar o valorar las líneas: {missing}."})

    items = []
    subtotal = Decimal("0")
    for line in lines:
        line_total = line.quantity * line.invoice_unit_price
        subtotal += line_total
        items.append({
            "line_number": line.line_number,
            "sku": line.siigo_sku,
            "description": line.invoice_description or line.original_description,
            "quantity": line.quantity,
            "unit_price": line.invoice_unit_price,
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
        "external_writes_enabled": False,
    }
