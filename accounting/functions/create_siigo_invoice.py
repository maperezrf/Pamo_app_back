import requests
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from integrations.siigo import SiigoAPIError, SiigoClient
from logistics.functions.change_remittance_state import change_remittance_state
from logistics.models import Remittance

from ..models import RemittanceInvoiceAttempt
from .build_invoice_preview import build_invoice_preview


def _build_siigo_payload(remittance, preview, *, document_id, seller_id, payment_type_id, payment_days):
    return {
        "document": {"id": document_id},
        "date": timezone.now().date().isoformat(),
        "customer": {"identification": remittance.customer.nit},
        "seller": seller_id,
        "items": [
            {
                "code": item["sku"],
                "description": item["description"],
                "quantity": float(item["quantity"]),
                "price": float(item["unit_price"]),
            }
            for item in preview["items"]
        ],
        "payments": [{"id": payment_type_id, "value": float(preview["subtotal"]), "due_date": payment_days}],
        "observations": f"Remisión {remittance.number}",
    }


def _mark_attempt(attempt, status, sanitized_result, *, external_invoice_id="", external_number=""):
    attempt.status = status
    attempt.sanitized_result = sanitized_result
    attempt.external_invoice_id = external_invoice_id
    attempt.external_number = external_number
    attempt.save(update_fields=["status", "sanitized_result", "external_invoice_id", "external_number", "updated_at"])


def create_siigo_invoice(remittance_id, actor, *, document_id, seller_id, payment_type_id, payment_days=15):
    """Emite en Siigo la factura de una remisión. document_id/seller_id/
    payment_type_id son obligatorios y los decide quien llama (vista) --
    no hay un valor fijo global, así distintas remisiones pueden facturarse
    con distinto vendedor/forma de pago sin tocar código.

    El gate de "¿se puede facturar esta remisión ahora?" lo resuelve por
    completo la máquina de estados (change_remittance_state a INVOICING) --
    no se duplica esa validación acá."""
    try:
        remittance = Remittance.objects.select_related("customer").prefetch_related("lines").get(pk=remittance_id)
    except Remittance.DoesNotExist as error:
        raise NotFound("La remisión no existe.") from error

    preview = build_invoice_preview(remittance)

    idempotency_key = f"{remittance.id}:{remittance.version}"
    existing_attempt = RemittanceInvoiceAttempt.objects.filter(idempotency_key=idempotency_key).first()
    if existing_attempt and existing_attempt.status == RemittanceInvoiceAttempt.Status.SUCCEEDED:
        return existing_attempt
    if existing_attempt and existing_attempt.status == RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT:
        raise ValidationError({
            "current_state": "El intento anterior quedó con resultado desconocido en Siigo; verifica "
            "manualmente antes de reintentar.",
        })

    remittance = change_remittance_state(remittance, Remittance.State.INVOICING, actor)

    attempt, _ = RemittanceInvoiceAttempt.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={"remittance": remittance, "status": RemittanceInvoiceAttempt.Status.PENDING, "created_by": actor},
    )

    payload = _build_siigo_payload(
        remittance, preview,
        document_id=document_id, seller_id=seller_id,
        payment_type_id=payment_type_id, payment_days=payment_days,
    )

    try:
        result = SiigoClient().create_invoice(payload)
    except SiigoAPIError as error:
        _mark_attempt(attempt, RemittanceInvoiceAttempt.Status.FAILED, {"error": str(error)})
        change_remittance_state(remittance, Remittance.State.INVOICE_FAILED, actor, metadata={"error": str(error)})
        raise
    except requests.RequestException as error:
        _mark_attempt(attempt, RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT, {"error": str(error)})
        change_remittance_state(
            remittance, Remittance.State.INVOICE_FAILED, actor,
            metadata={"error": str(error), "unknown_result": True},
        )
        raise SiigoAPIError(
            "No se pudo confirmar si Siigo recibió la factura; verifica manualmente antes de reintentar."
        ) from error

    _mark_attempt(
        attempt, RemittanceInvoiceAttempt.Status.SUCCEEDED,
        {"id": result.get("id"), "name": result.get("name"), "date": result.get("date")},
        external_invoice_id=str(result.get("id", "")),
        external_number=str(result.get("name", "")),
    )
    change_remittance_state(
        remittance, Remittance.State.INVOICED, actor, metadata={"external_invoice_id": attempt.external_invoice_id},
    )
    return attempt
