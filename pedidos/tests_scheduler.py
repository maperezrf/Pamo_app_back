from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from pedidos.models import IntegrationStatus
from pedidos.management.commands.run_orders_sync_scheduler import _date_window


SAFE_FLAGS = (
    ("ORDERS_LOCAL_MODE", True),
    ("ORDERS_EXTERNAL_READS_ENABLED", True),
    ("EXTERNAL_WRITES_ENABLED", False),
    ("ORDERS_EXTERNAL_WRITES_ENABLED", False),
)


class OrdersSyncSchedulerTests(TestCase):
    def setUp(self):
        for name, value in SAFE_FLAGS:
            patcher = patch(
                f"pedidos.management.commands.run_orders_sync_scheduler.{name}",
                value,
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch("pedidos.management.commands.run_orders_sync_scheduler.fcntl.flock")
    @patch("pedidos.management.commands.run_orders_sync_scheduler.call_command")
    def test_scheduler_runs_recent_read_only_cycle(self, mocked_import, _mocked_lock):
        call_command(
            "run_orders_sync_scheduler",
            interval_seconds=300,
            window_days=2,
        )

        today = timezone.localdate()
        utc_today = timezone.now().date()
        mocked_import.assert_called_once_with(
            "import_pamo_orders",
            from_date=(today - timedelta(days=1)).isoformat(),
            to_date=max(today, utc_today).isoformat(),
            workers=4,
            download_labels=True,
            label_workers=3,
        )
        status = IntegrationStatus.objects.get(provider="pamo_canonical")
        self.assertEqual(status.details["externalWrites"], 0)
        self.assertEqual(status.details["scheduler"]["state"], "available")
        self.assertEqual(status.details["scheduler"]["intervalSeconds"], 300)

    @patch("pedidos.management.commands.run_orders_sync_scheduler.time.sleep")
    @patch("pedidos.management.commands.run_orders_sync_scheduler.fcntl.flock")
    @patch("pedidos.management.commands.run_orders_sync_scheduler.call_command")
    def test_scheduler_uses_periodic_catchup_window(
        self,
        mocked_import,
        _mocked_lock,
        mocked_sleep,
    ):
        call_command(
            "run_orders_sync_scheduler",
            loop=True,
            max_cycles=2,
            interval_seconds=300,
            window_days=2,
            catchup_window_days=14,
            catchup_every_cycles=1,
        )

        today = timezone.localdate()
        utc_today = timezone.now().date()
        self.assertEqual(mocked_import.call_count, 2)
        self.assertEqual(
            mocked_import.call_args_list,
            [
                call(
                    "import_pamo_orders",
                    from_date=(today - timedelta(days=1)).isoformat(),
                    to_date=max(today, utc_today).isoformat(),
                    workers=4,
                    download_labels=True,
                    label_workers=3,
                ),
                call(
                    "import_pamo_orders",
                    from_date=(today - timedelta(days=13)).isoformat(),
                    to_date=max(today, utc_today).isoformat(),
                    workers=4,
                    download_labels=True,
                    label_workers=3,
                ),
            ],
        )
        mocked_sleep.assert_called_once_with(300)

    def test_date_window_covers_evening_orders_in_next_utc_day(self):
        from_date, to_date = _date_window(
            2,
            local_today=date(2026, 8, 27),
            utc_today=date(2026, 8, 28),
        )

        self.assertEqual(from_date, date(2026, 8, 26))
        self.assertEqual(to_date, date(2026, 8, 28))

    @patch("pedidos.management.commands.run_orders_sync_scheduler.fcntl.flock")
    @patch(
        "pedidos.management.commands.run_orders_sync_scheduler.call_command",
        side_effect=RuntimeError("provider unavailable"),
    )
    def test_failure_preserves_last_success_and_marks_data_stale(
        self,
        _mocked_import,
        _mocked_lock,
    ):
        previous_success = timezone.now() - timedelta(hours=1)
        IntegrationStatus.objects.create(
            provider="pamo_canonical",
            state="connected_read_only",
            last_success_at=previous_success,
            records_observed=1586,
            details={"externalWrites": 0},
        )

        with self.assertRaisesMessage(CommandError, "se conservaron los datos"):
            call_command("run_orders_sync_scheduler")

        status = IntegrationStatus.objects.get(provider="pamo_canonical")
        self.assertEqual(status.last_success_at, previous_success)
        self.assertEqual(status.records_observed, 1586)
        self.assertEqual(status.state, "stale_read_only")
        self.assertEqual(status.details["scheduler"]["state"], "stale")
        self.assertEqual(status.details["externalWrites"], 0)

    @patch(
        "pedidos.management.commands.run_orders_sync_scheduler.fcntl.flock",
        side_effect=BlockingIOError,
    )
    def test_scheduler_rejects_a_second_instance(self, _mocked_lock):
        with self.assertRaisesMessage(CommandError, "Ya existe un planificador"):
            call_command("run_orders_sync_scheduler")

    @patch(
        "pedidos.management.commands.run_orders_sync_scheduler.connection",
        SimpleNamespace(vendor="postgresql"),
    )
    def test_scheduler_rejects_non_local_database(self):
        with self.assertRaisesMessage(CommandError, "SQLite local"):
            call_command("run_orders_sync_scheduler")


class CanonicalImportSafetyTests(TestCase):
    @patch(
        "pedidos.management.commands.import_pamo_orders.ORDERS_EXTERNAL_READS_ENABLED",
        True,
    )
    @patch(
        "pedidos.management.commands.import_pamo_orders.EXTERNAL_WRITES_ENABLED",
        False,
    )
    @patch(
        "pedidos.management.commands.import_pamo_orders.ORDERS_EXTERNAL_WRITES_ENABLED",
        False,
    )
    @patch(
        "pedidos.management.commands.import_pamo_orders.connection",
        SimpleNamespace(vendor="postgresql"),
    )
    def test_import_rejects_non_local_database(self):
        with self.assertRaisesMessage(CommandError, "SQLite local"):
            call_command(
                "import_pamo_orders",
                from_date="2026-08-27",
                to_date="2026-08-27",
            )
