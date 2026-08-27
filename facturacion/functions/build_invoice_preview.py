from decimal import Decimal

from rest_framework.exceptions import ValidationError

try:
    from catalogo.models import SiigoProductSnapshot
except ModuleNotFoundError:  # El snapshot llega desde la rama independiente de Catálogo.
    SiigoProductSnapshot = None

from .siigo_invoice import customer_policy, stable_idempotency_key


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
        snapshot = (
            SiigoProductSnapshot.objects.filter(active=True, sku__iexact=line.siigo_sku).first()
            if SiigoProductSnapshot is not None else None
        )
        items.append({
            "line_number": line.line_number,
            "sku": line.siigo_sku,
            "description": line.invoice_description or line.original_description,
            "quantity": line.quantity,
            "unit_price": line.invoice_unit_price,
            "total": line_total,
            "local_tax_rate": snapshot.tax_rate if snapshot else None,
            "local_tax_included": snapshot.tax_included if snapshot else None,
        })
    policy = customer_policy(remittance.customer.nit)
    latest_preflight = remittance.invoice_attempts.filter(
        status="PREVIEWED",
    ).order_by("-updated_at").first()
    siigo_preflight = (
        latest_preflight.sanitized_result
        if latest_preflight and latest_preflight.sanitized_result.get("status") == "READY_FOR_CONTROLLED_DRAFT"
        else {
            "status": "PENDING_LIVE_VALIDATION",
            "detail": "Falta validar cliente, documento, vendedor, pago e impuestos contra la cuenta Siigo.",
        }
    )
    return {
        "remittance_id": remittance.id,
        "remittance_number": remittance.number,
        "customer": {"siigo_id": remittance.customer.siigo_id, "nit": remittance.customer.nit, "name": remittance.customer.name},
        "payment_method": "CREDIT",
        "payment_days": 15,
        "items": items,
        "subtotal": subtotal,
        "customer_policy": {
            "payment_days": policy["payment_days"],
            "retentions": [
                {
                    "type": item["type"],
                    "siigo_percentage": item["siigo_percentage"],
                    "calculation_percentage": item["calculation_percentage"],
                    "label": item["label"],
                    "evidence": item["evidence"],
                }
                for item in policy["retentions"]
            ],
        },
        "idempotency_key": stable_idempotency_key(remittance),
        "siigo_preflight": siigo_preflight,
        "external_writes_enabled": False,
    }
