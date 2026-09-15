from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from orchestrator.models import ProcessType

User = get_user_model()


class ProcessTypeListPermissionsTest(APITestCase):
    """Verifica que un usuario sin rol de orchestrator no pueda listar procesos."""

    def setUp(self):
        self.url = reverse("orchestrator-process-types")
        ProcessType.objects.create(
            code="test.process", name="Proceso de prueba", app_label="orchestrator"
        )

    def test_unauthenticated_user_cannot_access(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (401, 403))

    def test_user_without_role_cannot_access(self):
        user = User.objects.create_user(
            username="sin_rol", password="pass1234")
        self.client.force_authenticate(user=user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
