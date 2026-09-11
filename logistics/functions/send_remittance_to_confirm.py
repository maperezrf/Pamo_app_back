from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from ..models import Remittance
from .change_remittance_state import change_remittance_state


@transaction.atomic
def send_remittance_to_confirm(remittance_id, actor):
    try:
        remittance = Remittance.objects.select_for_update().prefetch_related("lines").get(pk=remittance_id)
    except Remittance.DoesNotExist as error:
        raise NotFound("La remisión no existe.") from error
    if not remittance.lines.exists():
        raise ValidationError({"lines": "La remisión no tiene productos."})
    change_remittance_state(remittance, Remittance.State.SENT_TO_CONFIRM, actor)
    return remittance
