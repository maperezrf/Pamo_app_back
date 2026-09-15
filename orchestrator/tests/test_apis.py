from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from rest_framework.test import APITestCase

from orchestrator.models import ProcessExecution, ProcessType

User = get_user_model()


class OrchestratorApiHappyPathTest(APITestCase):
    """Happy path: listar tipos de proceso y lanzar una ejecución."""

    def setUp(self):
        group = Group.objects.create(name="Operaciones")
        self.user = User.objects.create_user(
            username="operador", password="pass1234")
        self.user.groups.add(group)

        self.process_type = ProcessType.objects.create(
            code="test.process", name="Proceso de prueba", app_label="orchestrator"
        )

        self.client.force_authenticate(user=self.user)

    def test_listar_process_types(self):
        response = self.client.get(reverse("orchestrator-process-types"))

        self.assertEqual(response.status_code, 200)
        codes = [item["code"] for item in response.data]
        self.assertIn("test.process", codes)

    @patch("orchestrator.core.runner.ThreadProcessRunner.start")
    def test_launch_process_creates_execution_and_responds_202(self, mock_thread_start):
        url = reverse(
            "orchestrator-launch-process", kwargs={"code": self.process_type.code}
        )

        response = self.client.post(url, data={}, format="json")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(
            ProcessExecution.objects.filter(
                process_type=self.process_type).exists()
        )
        mock_thread_start.assert_called_once()
