from django.test import TestCase

from orchestrator.core.cancellation import (
    CancellationToken,
    ProcessCancelledException,
)
from orchestrator.core.constants import ProcessStatus
from orchestrator.models import ProcessExecution, ProcessType


class CancellationTokenTest(TestCase):
    def setUp(self):
        process_type = ProcessType.objects.create(
            code="test.cancel", name="Proceso cancelable", app_label="orchestrator"
        )
        self.execution = ProcessExecution.objects.create(
            process_type=process_type, params={}, status=ProcessStatus.RUNNING
        )
        self.token = CancellationToken(self.execution.pk)

    def test_not_cancelled_by_default(self):
        self.assertFalse(self.token.is_cancelled())
        self.token.raise_if_cancelled()  # no debe lanzar

    def test_checkpoint_raises_exception_if_cancel_requested(self):
        self.execution.cancellation_requested = True
        self.execution.save(update_fields=["cancellation_requested"])

        self.assertTrue(self.token.is_cancelled())
        with self.assertRaises(ProcessCancelledException):
            self.token.raise_if_cancelled()
