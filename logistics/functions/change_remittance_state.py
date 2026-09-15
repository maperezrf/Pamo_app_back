from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from ..models import Remittance, RemittanceState


class InvalidStateTransition(APIException):
    status_code = 409
    default_detail = "La remisión no puede pasar a ese estado desde su estado actual."
    default_code = "invalid_state_transition"


ALLOWED_TRANSITIONS = {
    Remittance.State.DRAFT: {Remittance.State.SENT_TO_CONFIRM, Remittance.State.CANCELLED},
    Remittance.State.SENT_TO_CONFIRM: {Remittance.State.CONFIRMED, Remittance.State.CANCELLED},
    Remittance.State.CONFIRMED: {Remittance.State.PENDING_INVOICE, Remittance.State.CANCELLED},
    Remittance.State.PENDING_INVOICE: {Remittance.State.INVOICING},
    Remittance.State.INVOICING: {Remittance.State.INVOICED, Remittance.State.INVOICE_FAILED},
    Remittance.State.INVOICE_FAILED: {Remittance.State.INVOICING},
    Remittance.State.INVOICED: set(),
    Remittance.State.CANCELLED: set(),
}


@transaction.atomic
def change_remittance_state(remittance, new_state, actor=None, metadata=None):
    """Única puerta de escritura para Remittance.current_state -- valida la
    transición contra ALLOWED_TRANSITIONS, agrega la fila de historial y
    actualiza el puntero denormalizado en la misma transacción."""
    allowed = ALLOWED_TRANSITIONS.get(remittance.current_state, set())
    if new_state not in allowed:
        raise InvalidStateTransition(
            f"No se puede pasar de {remittance.current_state} a {new_state}."
        )
    RemittanceState.objects.create(remittance=remittance, state=new_state, actor=actor, metadata=metadata or {})
    remittance.current_state = new_state
    remittance.current_state_at = timezone.now()
    remittance.save(update_fields=["current_state", "current_state_at"])
    return remittance
