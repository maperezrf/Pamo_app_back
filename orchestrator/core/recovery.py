"""Recuperación de ejecuciones huérfanas al reiniciar (requerimiento 6).

No intenta reanudar el proceso; solo marca como INTERRUMPIDO cualquier
ejecución que dependiera de un hilo o de la cola en memoria del proceso
Python anterior (ya perdidos al reiniciar):

- EJECUTANDO: el hilo que la corría ya no existe.
- CANCELANDO: esperaba que ese mismo hilo llegara a un checkpoint y nunca
  llegará, quedaría huérfana para siempre si no se marca aquí.
- EN_COLA: vivía únicamente en el `deque` en memoria de
  `concurrency_manager`, que se reinicia vacío en cada arranque.
"""

from django.utils import timezone

from .constants import ProcessStatus


def recover_orphan_executions():
    from ..models import ProcessExecution

    ProcessExecution.objects.filter(
        status__in=[
            ProcessStatus.RUNNING,
            ProcessStatus.CANCELLING,
            ProcessStatus.QUEUED,
        ]
    ).update(
        status=ProcessStatus.INTERRUPTED,
        finished_at=timezone.now(),
        error_message="Interrumpido por reinicio del servidor",
    )
