from django.test import TestCase

from orchestrator.core.constants import ProcessStatus
from orchestrator.core.recovery import recover_orphan_executions
from orchestrator.models import ProcessExecution, ProcessType


class RecoverOrphanExecutionsTest(TestCase):
    """Verifica que EJECUTANDO -> INTERRUMPIDO al reiniciar (requerimiento 6)."""

    def setUp(self):
        self.process_type = ProcessType.objects.create(
            code="test.recovery", name="Proceso huérfano", app_label="orchestrator"
        )

    def test_marks_orphan_executions_as_interrupted(self):
        orphan = ProcessExecution.objects.create(
            process_type=self.process_type, status=ProcessStatus.RUNNING
        )
        completed = ProcessExecution.objects.create(
            process_type=self.process_type, status=ProcessStatus.COMPLETED
        )

        recover_orphan_executions()

        orphan.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(orphan.status, ProcessStatus.INTERRUPTED)
        self.assertIsNotNone(orphan.finished_at)
        self.assertEqual(completed.status, ProcessStatus.COMPLETED)
