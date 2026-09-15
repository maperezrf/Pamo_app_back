"""API pública de `apps.orchestrator` para otras apps (bajo acoplamiento).

`launch_process` es el punto de entrada que deben usar los reportes/apps de
negocio en vez de importar el modelo o el `concurrency_manager` directamente
(ver requerimiento 9 / punto 9 del diseño).
"""

from django.utils import timezone

from rest_framework.exceptions import APIException


class ProcessAlreadyRunningError(APIException):
    status_code = 409
    default_detail = "Ya existe una ejecución en curso para este proceso."
    default_code = "process_already_running"


def launch_process(code, user=None, params=None, triggered_by_schedule=None):
    """Crea un `ProcessExecution` y lo entrega al `concurrency_manager`.

    Aplica la regla de duplicados (requerimiento 4): si el `ProcessType` no
    permite concurrencia y ya hay una ejecución activa, lanza
    `ProcessAlreadyRunningError` (409) sin crear una nueva fila.
    """
    from .core.concurrency_manager import concurrency_manager
    from .models import ProcessExecution, ProcessType

    process_type = ProcessType.objects.get(code=code, is_active=True)

    if not process_type.allow_concurrent and concurrency_manager.has_active_or_queued(
        process_type.id
    ):
        raise ProcessAlreadyRunningError()

    execution = ProcessExecution.objects.create(
        process_type=process_type,
        user=user,
        params=params or {},
        triggered_by_schedule=triggered_by_schedule,
    )
    concurrency_manager.submit(execution)
    return execution


def request_cancellation(execution):
    from .core.constants import ProcessStatus

    execution.cancellation_requested = True
    execution.status = ProcessStatus.CANCELLING
    execution.save(update_fields=["cancellation_requested", "status"])
    return execution
