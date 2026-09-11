from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, ValidationError

from ..models import Remittance, RemittanceSequence
from .change_remittance_state import ALLOWED_TRANSITIONS, InvalidStateTransition, change_remittance_state


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
    if Remittance.State.CONFIRMED not in ALLOWED_TRANSITIONS.get(remittance.current_state, set()):
        raise InvalidStateTransition(f"No se puede confirmar desde el estado {remittance.current_state}.")
    if not remittance.lines.exists():
        raise ValidationError({"lines": "La remisión no tiene productos."})

    sequence, _ = RemittanceSequence.objects.select_for_update().get_or_create(key="RD")
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    remittance.number = f"RD-{sequence.last_value:04d}"
    remittance.version += 1
    remittance.save(update_fields=["number", "version", "updated_at"])

    change_remittance_state(remittance, Remittance.State.CONFIRMED, actor)
    change_remittance_state(remittance, Remittance.State.PENDING_INVOICE, actor)
    return remittance
