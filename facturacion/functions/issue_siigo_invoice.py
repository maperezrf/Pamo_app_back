"""Emisión idempotente de un borrador Siigo previamente validado."""

from django.db import transaction

from ..models import Remittance, RemittanceAuditEvent, RemittanceInvoiceAttempt
from .siigo_invoice import SiigoWriteError, stable_idempotency_key


class SiigoIssuanceError(Exception):
    def __init__(self, message, *, code="SIIGO_ISSUANCE_ERROR", status_code=409, details=None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def _success_payload(attempt, *, reused=False):
    return {
        "status": "DRAFT_CREATED_IN_SIIGO",
        "reused": reused,
        "external_writes": 1,
        "dian_sent": False,
        "mail_sent": False,
        "idempotency_key": attempt.idempotency_key,
        "external_invoice_id": attempt.external_invoice_id,
        "external_number": attempt.external_number,
    }


def issue_controlled_siigo_draft(remittance, *, actor, client):
    expected_key = stable_idempotency_key(remittance)
    with transaction.atomic():
        locked = Remittance.objects.select_for_update().get(pk=remittance.pk)
        try:
            attempt = RemittanceInvoiceAttempt.objects.select_for_update().get(
                remittance=locked,
                idempotency_key=expected_key,
            )
        except RemittanceInvoiceAttempt.DoesNotExist as error:
            raise SiigoIssuanceError(
                "Primero valida nuevamente cliente, impuestos y payload en Siigo.",
                code="SIIGO_PREFLIGHT_REQUIRED",
                status_code=422,
            ) from error

        if attempt.status == RemittanceInvoiceAttempt.Status.SUCCEEDED:
            return _success_payload(attempt, reused=True)
        if attempt.status == RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT:
            raise SiigoIssuanceError(
                "El resultado anterior es desconocido. Debe reconciliarse en Siigo antes de reintentar.",
                code="SIIGO_RECONCILIATION_REQUIRED",
            )
        if attempt.status == RemittanceInvoiceAttempt.Status.PENDING:
            raise SiigoIssuanceError(
                "La creación de este borrador ya está en curso.",
                code="SIIGO_INVOICE_IN_PROGRESS",
            )
        preflight = attempt.sanitized_result
        if preflight.get("status") != "READY_FOR_CONTROLLED_DRAFT":
            raise SiigoIssuanceError(
                "La preliquidación guardada no está lista para crear un borrador.",
                code="SIIGO_PREFLIGHT_INVALID",
                status_code=422,
            )
        payload = preflight.get("payload") or {}
        if payload.get("stamp", {}).get("send") is not False or payload.get("mail", {}).get("send") is not False:
            raise SiigoIssuanceError(
                "El payload no está limitado a borrador sin DIAN ni correo.",
                code="SIIGO_DRAFT_ONLY",
                status_code=422,
            )
        attempt.status = RemittanceInvoiceAttempt.Status.PENDING
        attempt.save(update_fields=["status", "updated_at"])

    try:
        response = client.create_invoice(payload, idempotency_key=expected_key)
    except SiigoWriteError as error:
        with transaction.atomic():
            attempt = RemittanceInvoiceAttempt.objects.select_for_update().get(pk=attempt.pk)
            attempt.status = (
                RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT
                if error.outcome_unknown
                else RemittanceInvoiceAttempt.Status.FAILED
            )
            attempt.sanitized_result = {
                **attempt.sanitized_result,
                "write_error": {"code": error.code, "details": error.details},
            }
            attempt.save(update_fields=["status", "sanitized_result", "updated_at"])
            RemittanceAuditEvent.objects.create(
                remittance=attempt.remittance,
                event_type="SIIGO_DRAFT_RESULT_UNKNOWN" if error.outcome_unknown else "SIIGO_DRAFT_REJECTED",
                actor=actor,
                details={"code": error.code, "idempotency_key": expected_key, "external_writes": 1},
            )
        raise SiigoIssuanceError(
            error.message,
            code=error.code,
            status_code=502,
            details=error.details,
        ) from error

    external_id = str(response.get("id") or "")
    external_number = str(response.get("name") or response.get("number") or "")
    if not external_id:
        with transaction.atomic():
            attempt = RemittanceInvoiceAttempt.objects.select_for_update().get(pk=attempt.pk)
            attempt.status = RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT
            attempt.save(update_fields=["status", "updated_at"])
        raise SiigoIssuanceError(
            "Siigo respondió sin identificador. Requiere reconciliación antes de reintentar.",
            code="SIIGO_WRITE_RESULT_UNKNOWN",
            status_code=502,
        )

    with transaction.atomic():
        attempt = RemittanceInvoiceAttempt.objects.select_for_update().get(pk=attempt.pk)
        attempt.status = RemittanceInvoiceAttempt.Status.SUCCEEDED
        attempt.external_invoice_id = external_id
        attempt.external_number = external_number
        attempt.sanitized_result = {
            **attempt.sanitized_result,
            "write_result": {
                "id": external_id,
                "number": external_number,
                "status": str(response.get("stamp", {}).get("status") or "Draft"),
            },
        }
        attempt.save(update_fields=[
            "status", "external_invoice_id", "external_number", "sanitized_result", "updated_at",
        ])
        RemittanceAuditEvent.objects.create(
            remittance=attempt.remittance,
            event_type="SIIGO_DRAFT_CREATED",
            actor=actor,
            details={
                "external_invoice_id": external_id,
                "external_number": external_number,
                "idempotency_key": expected_key,
                "dian_sent": False,
                "mail_sent": False,
                "external_writes": 1,
            },
        )
    return _success_payload(attempt)
