from ..models import RemittanceInvoiceLine


def ensure_invoice_lines(remittance):
    """Garantiza que cada RemittanceLine de la remisión tenga su fila
    RemittanceInvoiceLine (aunque esté vacía) para que contabilidad pueda
    codificarla desde /admin/. Devuelve las filas en el mismo orden que
    remittance.lines."""
    lines = list(remittance.lines.all())
    existing = {
        invoice_line.remittance_line_id: invoice_line
        for invoice_line in RemittanceInvoiceLine.objects.filter(remittance_line__remittance=remittance)
    }
    missing_lines = [line for line in lines if line.pk not in existing]
    if missing_lines:
        RemittanceInvoiceLine.objects.bulk_create(
            [RemittanceInvoiceLine(remittance_line=line) for line in missing_lines]
        )
        existing = {
            invoice_line.remittance_line_id: invoice_line
            for invoice_line in RemittanceInvoiceLine.objects.filter(remittance_line__remittance=remittance)
        }
    return [existing[line.pk] for line in lines]
