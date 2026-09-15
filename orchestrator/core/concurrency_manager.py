"""Control de concurrencia y cola FIFO en memoria (requerimientos 3 y 4).

Singleton por proceso Django (instancia a nivel de módulo, protegida con
`threading.Lock`). NO es multi-proceso/multi-nodo: si el despliegue usa más de
un worker WSGI, cada worker tiene su propio estado — ver "Riesgos" en
`docs/implementation-plans/process-orchestrator-module.md`.
"""

import threading
from collections import deque

from django.conf import settings

from .constants import DEFAULT_MAX_CONCURRENT_EXECUTIONS, ProcessStatus


def _max_concurrent_executions():
    orchestrator_settings = getattr(
        settings, "ORCHESTRATOR_SETTINGS", {}) or {}
    return orchestrator_settings.get(
        "MAX_CONCURRENT_EXECUTIONS", DEFAULT_MAX_CONCURRENT_EXECUTIONS
    )


class ConcurrencyManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._active_count = 0
        self._queue = deque()

    def has_active_or_queued(self, process_type_id):
        from ..models import ProcessExecution

        return ProcessExecution.objects.filter(
            process_type_id=process_type_id,
            status__in=ProcessStatus.ACTIVE,
        ).exists()

    def submit(self, execution):
        """Recibe un `ProcessExecution` en PENDIENTE y decide si arranca ya o se encola."""
        from .runner import ThreadProcessRunner

        max_concurrent = execution.process_type.max_concurrent_global or (
            _max_concurrent_executions()
        )

        with self._lock:
            if self._active_count < max_concurrent:
                self._active_count += 1
                start_now = True
            else:
                self._queue.append(execution.pk)
                start_now = False

        if start_now:
            execution.status = ProcessStatus.RUNNING
            execution.save(update_fields=["status"])
            ThreadProcessRunner(execution_id=execution.pk).start()
        else:
            execution.status = ProcessStatus.QUEUED
            execution.save(update_fields=["status"])

    def on_finished(self, execution_id):
        """Libera el slot y arranca el siguiente de la cola (si existe)."""
        from .runner import ThreadProcessRunner
        from ..models import ProcessExecution

        next_execution_id = None
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            max_concurrent = _max_concurrent_executions()
            if self._queue and self._active_count < max_concurrent:
                next_execution_id = self._queue.popleft()
                self._active_count += 1

        if next_execution_id is not None:
            ProcessExecution.objects.filter(pk=next_execution_id).update(
                status=ProcessStatus.RUNNING
            )
            ThreadProcessRunner(execution_id=next_execution_id).start()


# Instancia única a nivel de módulo (singleton por proceso Django).
concurrency_manager = ConcurrencyManager()
