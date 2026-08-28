import json
from subprocess import CompletedProcess
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import CatalogHistoryEvent, IntegrationReadStatus


class EnviaConnectionCheckTests(TestCase):
    @patch("catalogo.management.commands.check_envia_connection.subprocess.run")
    def test_authenticated_read_records_available_without_external_writes(self, run):
        run.return_value = CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"ok": True, "http_status": 200, "carrier_count": 7}),
            stderr="",
        )

        call_command("check_envia_connection", "--execute-read")

        status = IntegrationReadStatus.objects.get(
            system="ENVIA", capability="shipping_api_connection",
        )
        self.assertEqual(status.status, IntegrationReadStatus.Status.AVAILABLE)
        self.assertEqual(status.record_count, 7)
        self.assertEqual(status.external_writes, 0)
        event = CatalogHistoryEvent.objects.get(entity_type="ShippingConnector")
        self.assertEqual(event.action, "CONNECTION_CHECK")
        self.assertEqual(event.after["external_writes"], 0)
        self.assertIn("process.env.ENVIA_SHIPPING_API_TOKEN", run.call_args.kwargs["input"])

    @patch("catalogo.management.commands.check_envia_connection.subprocess.run")
    def test_failed_read_is_visible_and_preserves_last_success(self, run):
        previous = IntegrationReadStatus.objects.create(
            system="ENVIA",
            capability="shipping_api_connection",
            status=IntegrationReadStatus.Status.AVAILABLE,
            message="Lectura anterior correcta.",
            observed_at=timezone.now(),
            last_success_at=timezone.now(),
            external_writes=0,
        )
        previous_success = previous.last_success_at
        run.return_value = CompletedProcess(
            args=[], returncode=3,
            stdout=json.dumps({"ok": False, "http_status": 401, "carrier_count": 0}),
            stderr="",
        )

        with self.assertRaisesMessage(CommandError, "Envía no quedó verificado"):
            call_command("check_envia_connection", "--execute-read")

        status = IntegrationReadStatus.objects.get(
            system="ENVIA", capability="shipping_api_connection",
        )
        self.assertEqual(status.status, IntegrationReadStatus.Status.BLOCKED)
        self.assertEqual(status.last_success_at, previous_success)
        self.assertEqual(status.external_writes, 0)
