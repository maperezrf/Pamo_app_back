from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .connections import build_connections_workspace
from .models import IntegrationReadStatus


class ConnectionsWorkspaceTests(TestCase):
    def test_reports_real_dates_and_disconnected_sources(self):
        observed_at = timezone.now() - timedelta(hours=2)
        IntegrationReadStatus.objects.create(
            system="SHOPIFY",
            capability="catalog_snapshot",
            status=IntegrationReadStatus.Status.AVAILABLE,
            message="Lectura correcta",
            record_count=12,
            observed_at=observed_at,
            last_success_at=observed_at,
            external_writes=0,
        )

        workspace = build_connections_workspace()
        shopify = next(row for row in workspace["connections"] if row["code"] == "SHOPIFY")
        taumm = next(row for row in workspace["connections"] if row["code"] == "TAUMM")

        self.assertEqual(shopify["status"], "CONNECTED")
        self.assertEqual(shopify["record_count"], 12)
        self.assertIsNotNone(shopify["next_scheduled_at"])
        self.assertEqual(taumm["status"], "DISCONNECTED")
        self.assertEqual(workspace["external_writes"], 0)

    def test_marks_scheduled_source_stale_after_two_intervals(self):
        observed_at = timezone.now() - timedelta(hours=13)
        IntegrationReadStatus.objects.create(
            system="FALABELLA",
            capability="catalog_snapshot",
            status=IntegrationReadStatus.Status.AVAILABLE,
            message="Lectura antigua",
            observed_at=observed_at,
            last_success_at=observed_at,
            external_writes=0,
        )
        workspace = build_connections_workspace()
        falabella = next(row for row in workspace["connections"] if row["code"] == "FALABELLA")
        self.assertEqual(falabella["status"], "STALE")
        self.assertTrue(falabella["stale"])

    @patch("catalogo.management.commands.run_connector_scheduler.fcntl.flock")
    @patch("catalogo.management.commands.run_connector_scheduler.subprocess.run")
    def test_scheduler_records_heartbeat_without_external_writes(self, mocked_run, _mocked_lock):
        mocked_run.return_value.returncode = 0
        mocked_run.return_value.stderr = ""
        call_command(
            "run_connector_scheduler",
            connectors="SHOPIFY",
            force=True,
        )
        self.assertEqual(mocked_run.call_count, 1)
        self.assertIn("refresh_shopify_snapshot", mocked_run.call_args.args[0])
        heartbeat = IntegrationReadStatus.objects.get(
            system="CATALOG", capability="connector_scheduler"
        )
        self.assertEqual(heartbeat.status, IntegrationReadStatus.Status.AVAILABLE)
        self.assertEqual(heartbeat.external_writes, 0)

    @patch("catalogo.management.commands.run_connector_scheduler.fcntl.flock")
    @patch("catalogo.management.commands.run_connector_scheduler.subprocess.run")
    def test_scheduler_runs_envia_authenticated_read_every_six_hours(self, mocked_run, _mocked_lock):
        mocked_run.return_value.returncode = 0
        mocked_run.return_value.stderr = ""

        call_command(
            "run_connector_scheduler",
            connectors="ENVIA",
            force=True,
        )

        command = mocked_run.call_args.args[0]
        self.assertIn("check_envia_connection", command)
        self.assertIn("--execute-read", command)
        workspace = build_connections_workspace()
        envia = next(row for row in workspace["connections"] if row["code"] == "ENVIA")
        self.assertEqual(envia["cadence_hours"], 6)

    @patch("catalogo.management.commands.refresh_taumm_snapshot.urlopen")
    def test_taumm_reader_persists_sanitized_real_status(self, mocked_urlopen):
        response = BytesIO(
            b'{"enabled":true,"intervalHours":4,"sourceMode":"night_category_listings",'
            b'"products":{"total":532,"active":528,"missing":4,"pending":0,'
            b'"lastCheckedAt":"2026-08-27T23:00:00Z","lastChangedAt":"2026-08-27T23:00:00Z"},'
            b'"runs":[{"id":"run-1","state":"completed","recordsRead":532,'
            b'"recordsValid":532,"recordsInvalid":0,"priceChanges":12,"inventoryChanges":8,'
            b'"startedAt":"2026-08-27T22:59:00Z","finishedAt":"2026-08-27T23:00:00Z"}],'
            b'"externalWrites":0}'
        )
        mocked_urlopen.return_value.__enter__.return_value = response

        call_command(
            "refresh_taumm_snapshot",
            base_url="https://beta.example.test",
            token="not-persisted",
        )

        status = IntegrationReadStatus.objects.get(
            system="TAUMM", capability="inventory_price_poller"
        )
        self.assertEqual(status.status, IntegrationReadStatus.Status.AVAILABLE)
        self.assertEqual(status.record_count, 528)
        self.assertEqual(status.external_writes, 0)
        self.assertNotIn("not-persisted", str(status.details))
        workspace = build_connections_workspace()
        taumm = next(row for row in workspace["connections"] if row["code"] == "TAUMM")
        self.assertEqual(taumm["status"], "CONNECTED")
        self.assertEqual(taumm["record_count"], 528)
