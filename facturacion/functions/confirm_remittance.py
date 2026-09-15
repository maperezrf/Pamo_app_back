from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from facturacion.models import Remittance, RemittanceAuditEvent, RemittanceSequence


class VersionConflict(APIException):
    status_code = 409
    default_detail = "La remisión cambió; recarga antes de confirmar."
    default_code = "version_conflict"


@transaction.atomic
def confirm_remittance(remittance_id, expected_version, actor):
    try:
        remittance = Remittance.objects.select_for_update().prefetch_related("lines").get(pk=remittance_id)
    except Remittance.DoesNotExist as error:
        raise NotFound("La remisión no existe.") from error
    if remittance.version != expected_version:
        raise VersionConflict()
    if remittance.document_status != Remittance.DocumentStatus.DRAFT:
        raise ValidationError({"document_status": "Solo se confirma un borrador."})
    if not remittance.lines.exists():
        raise ValidationError({"lines": "La remisión no tiene productos."})

    sequence, _ = RemittanceSequence.objects.select_for_update().get_or_create(key="RD")
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    remittance.number = f"RD-{sequence.last_value:04d}"
    remittance.document_status = Remittance.DocumentStatus.CONFIRMED
    remittance.confirmed_at = timezone.now()
    remittance.version += 1
    remittance.save(update_fields=["number", "document_status", "confirmed_at", "version", "updated_at"])
    RemittanceAuditEvent.objects.create(remittance=remittance, event_type="REMITTANCE_CONFIRMED", actor=actor)
    return remittance
