from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from ..models import Remittance
from .change_remittance_state import change_remittance_state


@transaction.atomic
def cancel_remittance(remittance_id, actor, reason):
    try:
        remittance = Remittance.objects.select_for_update().get(pk=remittance_id)
    except Remittance.DoesNotExist as error:
        raise NotFound("La remisión no existe.") from error
    if not reason:
        raise ValidationError({"reason": "Indica el motivo de la anulación."})
    change_remittance_state(remittance, Remittance.State.CANCELLED, actor, metadata={"reason": reason})
    return remittance
