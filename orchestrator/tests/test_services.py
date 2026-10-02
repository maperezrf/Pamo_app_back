from django.test import TestCase

from orchestrator.models import ProcessExecution, ProcessType
from orchestrator.services import get_execution_status


class GetExecutionStatusTest(TestCase):
    def test_returns_status_and_progress_of_an_existing_execution(self):
        process_type = ProcessType.objects.create(code="test.process", name="Test", app_label="test")
        execution = ProcessExecution.objects.create(
            process_type=process_type, status="EJECUTANDO", progress_percent=40, current_step="Paso 2"
        )

        self.assertEqual(
            get_execution_status(execution.pk),
            {"status": "EJECUTANDO", "progress_percent": 40, "current_step": "Paso 2", "error_message": None},
        )

    def test_returns_none_when_it_does_not_exist(self):
        self.assertIsNone(get_execution_status(999))
