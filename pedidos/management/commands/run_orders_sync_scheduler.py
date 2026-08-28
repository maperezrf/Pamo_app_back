import fcntl
import re
import tempfile
import time
from datetime import timedelta
from hashlib import sha256
from os import environ
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from config.constants import (
    EXTERNAL_WRITES_ENABLED,
    ORDERS_EXTERNAL_READS_ENABLED,
    ORDERS_EXTERNAL_WRITES_ENABLED,
    ORDERS_LOCAL_MODE,
)
from pedidos.models import IntegrationStatus


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _window_for_cycle(cycle_index, frequent_days, catchup_days, catchup_every):
    is_catchup = cycle_index > 0 and cycle_index % catchup_every == 0
    return (catchup_days if is_catchup else frequent_days), is_catchup


def _safe_error_code(error):
    name = re.sub(r"[^A-Z0-9_]+", "_", type(error).__name__.upper()).strip("_")
    return f"AUTO_SYNC_{name or 'FAILED'}"[:120]


class Command(BaseCommand):
    help = (
        "Actualiza Pedidos desde la API canónica en intervalos controlados; "
        "nunca ejecuta escrituras externas."
    )

    def add_arguments(self, parser):
        configured_minutes = _positive_int(environ.get("ORDERS_SYNC_INTERVAL_MINUTES"), 5)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval-seconds", type=int, default=configured_minutes * 60)
        parser.add_argument("--window-days", type=int, default=2)
        parser.add_argument("--catchup-window-days", type=int, default=14)
        parser.add_argument("--catchup-every-cycles", type=int, default=72)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--label-workers", type=int, default=3)
        parser.add_argument("--skip-labels", action="store_true")
        parser.add_argument("--max-cycles", type=int, default=0)

    def handle(self, *args, **options):
        self._assert_safe_runtime()
        interval_seconds = max(options["interval_seconds"], 60)
        frequent_days = min(max(options["window_days"], 1), 14)
        catchup_days = min(
            max(options["catchup_window_days"], frequent_days),
            93,
        )
        catchup_every = max(options["catchup_every_cycles"], 1)
        max_cycles = max(options["max_cycles"], 0)
        self._acquire_lock()

        cycle_index = 0
        while True:
            window_days, is_catchup = _window_for_cycle(
                cycle_index,
                frequent_days,
                catchup_days,
                catchup_every,
            )
            succeeded = self._cycle(
                window_days=window_days,
                is_catchup=is_catchup,
                interval_seconds=interval_seconds,
                workers=min(max(options["workers"], 1), 10),
                label_workers=min(max(options["label_workers"], 1), 5),
                download_labels=not options["skip_labels"],
            )
            cycle_index += 1

            if not options["loop"]:
                if not succeeded:
                    raise CommandError(
                        "La actualización falló; se conservaron los datos del último ciclo correcto."
                    )
                return
            if max_cycles and cycle_index >= max_cycles:
                return
            time.sleep(interval_seconds)

    def _assert_safe_runtime(self):
        if not ORDERS_LOCAL_MODE:
            raise CommandError("El planificador automático sólo está permitido en modo local.")
        if connection.vendor != "sqlite":
            raise CommandError("El planificador automático sólo puede usar SQLite local.")
        if not ORDERS_EXTERNAL_READS_ENABLED:
            raise CommandError("La lectura canónica debe estar habilitada para el planificador.")
        if EXTERNAL_WRITES_ENABLED or ORDERS_EXTERNAL_WRITES_ENABLED:
            raise CommandError("Todas las compuertas de escritura externa deben permanecer apagadas.")

    def _acquire_lock(self):
        database_name = str(connection.settings_dict.get("NAME") or settings.BASE_DIR)
        identity = sha256(database_name.encode("utf-8")).hexdigest()[:16]
        lock_path = Path(tempfile.gettempdir()) / f"pamo-orders-sync-{identity}.lock"
        self._lock_file = lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CommandError("Ya existe un planificador de Pedidos para esta base local.") from error

    def _cycle(
        self,
        *,
        window_days,
        is_catchup,
        interval_seconds,
        workers,
        label_workers,
        download_labels,
    ):
        started_at = timezone.now()
        to_date = timezone.localdate()
        from_date = to_date - timedelta(days=window_days - 1)
        try:
            call_command(
                "import_pamo_orders",
                from_date=from_date.isoformat(),
                to_date=to_date.isoformat(),
                workers=workers,
                download_labels=download_labels,
                label_workers=label_workers,
            )
        except Exception as error:
            self._record_failure(
                error=error,
                attempted_at=started_at,
                from_date=from_date,
                to_date=to_date,
                interval_seconds=interval_seconds,
                is_catchup=is_catchup,
            )
            self.stderr.write(
                self.style.WARNING(
                    "Actualización no disponible; se conservaron los datos anteriores. "
                    f"Código={_safe_error_code(error)}; externalWrites=0."
                )
            )
            return False

        finished_at = timezone.now()
        status, _ = IntegrationStatus.objects.get_or_create(provider="pamo_canonical")
        details = dict(status.details or {})
        details["externalWrites"] = 0
        details["scheduler"] = {
            "state": "available",
            "intervalSeconds": interval_seconds,
            "windowFrom": from_date.isoformat(),
            "windowTo": to_date.isoformat(),
            "catchup": is_catchup,
            "lastCycleAt": finished_at.isoformat(),
            "nextCycleAt": (finished_at + timedelta(seconds=interval_seconds)).isoformat(),
        }
        status.details = details
        status.last_attempt_at = finished_at
        status.last_success_at = finished_at
        status.last_error_code = ""
        status.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"Pedidos actualizados {from_date.isoformat()}..{to_date.isoformat()}; "
                "externalWrites=0."
            )
        )
        return True

    def _record_failure(
        self,
        *,
        error,
        attempted_at,
        from_date,
        to_date,
        interval_seconds,
        is_catchup,
    ):
        status, _ = IntegrationStatus.objects.get_or_create(provider="pamo_canonical")
        details = dict(status.details or {})
        details["externalWrites"] = 0
        details["scheduler"] = {
            "state": "stale",
            "intervalSeconds": interval_seconds,
            "windowFrom": from_date.isoformat(),
            "windowTo": to_date.isoformat(),
            "catchup": is_catchup,
            "lastAttemptAt": attempted_at.isoformat(),
            "errorCode": _safe_error_code(error),
        }
        status.state = "stale_read_only"
        status.details = details
        status.last_attempt_at = attempted_at
        status.last_error_code = _safe_error_code(error)
        status.save()
