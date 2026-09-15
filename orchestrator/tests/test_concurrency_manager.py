from django.test import TestCase

from orchestrator.core.concurrency_manager import ConcurrencyManager
from orchestrator.core.constants import ProcessStatus
from orchestrator.models import ProcessExecution, ProcessType


class ConcurrencyManagerTest(TestCase):
    """Valida límite de concurrencia y cola FIFO (sin lanzar hilos reales)."""

    def setUp(self):
        self.process_type = ProcessType.objects.create(
            code="test.process",
            name="Proceso de prueba",
            app_label="orchestrator",
            max_concurrent_global=1,
        )
        self.manager = ConcurrencyManager()

    def _create_execution(self):
        return ProcessExecution.objects.create(
            process_type=self.process_type, params={}
        )

    def test_first_execution_starts_immediately(self):
        execution = self._create_execution()

        with self._patch_runner() as mock_runner:
            self.manager.submit(execution)

        execution.refresh_from_db()
        self.assertEqual(execution.status, ProcessStatus.RUNNING)
        mock_runner.assert_called_once()

    def test_second_execution_queues_if_no_slot_available(self):
        execution_1 = self._create_execution()
        execution_2 = self._create_execution()

        with self._patch_runner():
            self.manager.submit(execution_1)
            self.manager.submit(execution_2)

        execution_2.refresh_from_db()
        self.assertEqual(execution_2.status, ProcessStatus.QUEUED)

    def test_on_finished_starts_next_in_queue(self):
        execution_1 = self._create_execution()
        execution_2 = self._create_execution()

        with self._patch_runner() as mock_runner:
            self.manager.submit(execution_1)
            self.manager.submit(execution_2)
            mock_runner.reset_mock()

            self.manager.on_finished(execution_1.pk)

        execution_2.refresh_from_db()
        self.assertEqual(execution_2.status, ProcessStatus.RUNNING)
        mock_runner.assert_called_once()

    def _patch_runner(self):
        from unittest.mock import patch

        return patch("orchestrator.core.runner.ThreadProcessRunner.start")
