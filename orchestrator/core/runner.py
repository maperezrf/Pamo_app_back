"""Hilo administrado que ejecuta un proceso registrado (pieza central, req. 1/2/5)."""

import logging
import threading
import time

from django.utils import timezone

from .cancellation import CancellationToken, ProcessCancelledException
from .constants import ProcessStatus
from .registry import get_process_callable

logger = logging.getLogger(__name__)


class ThreadProcessRunner(threading.Thread):
    def __init__(self, execution_id):
        super().__init__(daemon=True)
        self.execution_id = execution_id
        self.cancellation_token = CancellationToken(execution_id)

    def _update_progress(self, percent, step=None):
        from ..models import ProcessExecution

        fields = {"progress_percent": percent}
        if step is not None:
            fields["current_step"] = step
        ProcessExecution.objects.filter(pk=self.execution_id).update(**fields)

    def run(self):
        from .concurrency_manager import concurrency_manager
        from ..models import ProcessExecution

        execution = ProcessExecution.objects.select_related("process_type").get(
            pk=self.execution_id
        )
        callable_fn = get_process_callable(execution.process_type.code)

        started_at = timezone.now()
        execution.status = ProcessStatus.RUNNING
        execution.started_at = started_at
        execution.save(update_fields=["status", "started_at"])

        start_time = time.monotonic()
        try:
            if callable_fn is None:
                raise RuntimeError(
                    f"No hay callable registrado para el proceso "
                    f"'{execution.process_type.code}'. Ver core/registry.py."
                )

            callable_fn(
                params=execution.params or {},
                progress_callback=self._update_progress,
                cancellation_token=self.cancellation_token,
            )

            execution.status = ProcessStatus.COMPLETED
        except ProcessCancelledException as exc:
            execution.status = ProcessStatus.CANCELLED
            execution.error_message = str(exc)
        except SystemExit:
            # `SystemExit` no hereda de `Exception`, así que no la captura
            # el `except Exception` de abajo. Si un callable registrado
            # llamara `sys.exit()` (por ejemplo, indirectamente vía una
            # librería de terceros), sin este `except` `execution.status`
            # quedaría "congelado" en EJECUTANDO para siempre, bloqueando
            # relanzamientos futuros de ese `process_type` (ver
            # `concurrency_manager.has_active_or_queued`).
            if self.cancellation_token.is_cancelled():
                execution.status = ProcessStatus.CANCELLED
                execution.error_message = "Ejecución cancelada por el usuario."
            else:
                execution.status = ProcessStatus.ERROR
                execution.error_message = (
                    "El proceso finalizó inesperadamente (sys.exit) durante "
                    "el manejo de un error. Revisar logs/correo de error."
                )
        except Exception as exc:  # noqa: BLE001
            execution.status = ProcessStatus.ERROR
            execution.error_message = str(exc)
            logger.exception("Error en proceso %s", execution.process_type.code)
        finally:
            execution.finished_at = timezone.now()
            execution.duration_seconds = time.monotonic() - start_time
            try:
                execution.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "duration_seconds",
                        "error_message",
                    ]
                )
            except Exception as save_exc:  # noqa: BLE001
                # Si el guardado falla (ej. "database is locked" en SQLite
                # por escritura concurrente con el request de cancelación),
                # no debe impedir liberar el slot de concurrencia: de lo
                # contrario la ejecución queda "fantasma" bloqueando
                # relanzamientos futuros de este process_type.
                logger.exception(
                    "No se pudo guardar el estado final de %s", execution.process_type.code
                )
                try:
                    ProcessExecution.objects.filter(pk=self.execution_id).update(
                        status=execution.status,
                        finished_at=execution.finished_at,
                        duration_seconds=execution.duration_seconds,
                        error_message=execution.error_message,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Reintento de guardado también falló para %s",
                        execution.process_type.code,
                    )
            concurrency_manager.on_finished(self.execution_id)
