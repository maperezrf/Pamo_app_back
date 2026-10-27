from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from orchestrator.models import ProcessExecution, ProcessScheduleConfig, ProcessType

User = get_user_model()


class ExecutionListApiTest(APITestCase):
    """Listado de ejecuciones: paginado, con nombre del proceso y filtros."""

    def setUp(self):
        group = Group.objects.create(name="Operaciones")
        self.user = User.objects.create_user(
            username="operador", email="operador@pamo.co", password="pass1234")
        self.user.groups.add(group)
        self.client.force_authenticate(user=self.user)

        self.process_type = ProcessType.objects.create(
            code="test.process", name="Proceso de prueba", app_label="orchestrator"
        )
        self.schedule = ProcessScheduleConfig.objects.create(
            process_type=self.process_type, schedule_kind="CRON", cron_expression="0 5 * * *"
        )
        for _ in range(25):
            ProcessExecution.objects.create(process_type=self.process_type, user=self.user)
        ProcessExecution.objects.create(
            process_type=self.process_type, triggered_by_schedule=self.schedule
        )
        self.url = reverse("orchestrator-execution-list")

    def test_lista_paginada_con_nombre_y_usuario(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 26)
        self.assertEqual(len(response.data["results"]), 20)
        manual = next(r for r in response.data["results"] if r["user"])
        self.assertEqual(manual["process_type_name"], "Proceso de prueba")
        self.assertEqual(manual["user_email"], "operador@pamo.co")

    def test_filtra_por_programacion(self):
        response = self.client.get(self.url, {"schedule": self.schedule.pk})

        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["results"][0]["user_email"])

    def test_usuario_sin_rol_no_puede_listar(self):
        self.client.force_authenticate(
            user=User.objects.create_user(username="sin_rol", password="pass1234"))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)


@patch("orchestrator.core.scheduler.register_schedule")
class ScheduleApiTest(APITestCase):
    """CRUD de programaciones: validación por tipo y permisos."""

    def setUp(self):
        group = Group.objects.create(name="Operaciones")
        self.user = User.objects.create_user(username="operador", password="pass1234")
        self.user.groups.add(group)
        self.client.force_authenticate(user=self.user)

        self.process_type = ProcessType.objects.create(
            code="test.process", name="Proceso de prueba", app_label="orchestrator"
        )
        self.url = reverse("orchestrator-schedule-list")

    def test_crea_diaria_y_limpia_campos_ajenos(self, register_schedule):
        response = self.client.post(self.url, {
            "process_type": self.process_type.pk,
            "schedule_kind": "DAILY",
            "run_at_time": "06:00:00",
            "cron_expression": "0 5 * * *",
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["process_type_name"], "Proceso de prueba")
        self.assertIsNone(response.data["cron_expression"])
        self.assertIn("next_run_at", response.data)
        register_schedule.assert_called_once()

    def test_rechaza_campos_faltantes_por_tipo(self, register_schedule):
        response = self.client.post(self.url, {
            "process_type": self.process_type.pk,
            "schedule_kind": "WEEKLY",
            "run_at_time": "06:00:00",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("weekday", response.data)
        register_schedule.assert_not_called()

    def test_rechaza_cron_invalido(self, register_schedule):
        response = self.client.post(self.url, {
            "process_type": self.process_type.pk,
            "schedule_kind": "CRON",
            "cron_expression": "cada hora",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("cron_expression", response.data)

    def test_rechaza_fecha_unica_pasada(self, register_schedule):
        yesterday = timezone.localdate() - timedelta(days=1)
        response = self.client.post(self.url, {
            "process_type": self.process_type.pk,
            "schedule_kind": "ONCE_DATE",
            "run_at_date": yesterday.isoformat(),
            "run_at_time": "06:00:00",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("run_at_date", response.data)

    def test_pausa_con_patch_parcial(self, register_schedule):
        schedule = ProcessScheduleConfig.objects.create(
            process_type=self.process_type, schedule_kind="CRON", cron_expression="0 5 * * *"
        )

        response = self.client.patch(
            reverse("orchestrator-schedule-detail", args=[schedule.pk]),
            {"is_active": False}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertEqual(schedule.cron_expression, "0 5 * * *")

    def test_elimina(self, register_schedule):
        schedule = ProcessScheduleConfig.objects.create(
            process_type=self.process_type, schedule_kind="CRON", cron_expression="0 5 * * *"
        )

        response = self.client.delete(reverse("orchestrator-schedule-detail", args=[schedule.pk]))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(ProcessScheduleConfig.objects.exists())

    def test_usuario_sin_rol_no_puede_crear(self, register_schedule):
        self.client.force_authenticate(
            user=User.objects.create_user(username="sin_rol", password="pass1234"))

        response = self.client.post(self.url, {
            "process_type": self.process_type.pk,
            "schedule_kind": "DAILY",
            "run_at_time": "06:00:00",
        }, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProcessScheduleConfig.objects.exists())
