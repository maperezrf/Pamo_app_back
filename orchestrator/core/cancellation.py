"""Excepción y token de cancelación cooperativa (requerimiento 5).

La cancelación NO mata el hilo: el proceso registrado debe consultar
periódicamente `token.raise_if_cancelled()` en los checkpoints que le
correspondan (ver `docs/patterns/process-orchestrator-pattern.md`).
"""


class ProcessCancelledException(Exception):
    """Se lanza cuando un checkpoint detecta que se solicitó cancelar."""


class CancellationToken:
    def __init__(self, execution_id):
        self.execution_id = execution_id

    def is_cancelled(self) -> bool:
        # Import diferido para evitar dependencias circulares entre
        # core/ y models.py al momento de cargar la app.
        from ..models import ProcessExecution

        return ProcessExecution.objects.filter(
            pk=self.execution_id, cancellation_requested=True
        ).exists()

    def raise_if_cancelled(self):
        if self.is_cancelled():
            raise ProcessCancelledException(
                f"Ejecución #{self.execution_id} cancelada por el usuario."
            )
