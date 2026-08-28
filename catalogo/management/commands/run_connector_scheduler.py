import fcntl
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogo.connections import SCHEDULED_CONNECTORS, scheduled_connector_due
from catalogo.models import IntegrationReadStatus


def _safe_message(value):
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)(token|secret|key|password|authorization)\s*[=:]\s*\S+",
        r"\1=[oculto]",
        text,
    )
    return text[-500:]


class Command(BaseCommand):
    help = "Planificador local de lecturas multicanal; nunca ejecuta escrituras externas."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--connectors", default="")
        parser.add_argument("--interval-seconds", type=int, default=300)

    def handle(self, *args, **options):
        lock_path = Path(tempfile.gettempdir()) / "pamo-catalog-connector-scheduler.lock"
        self._lock_file = lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CommandError("Ya existe un planificador del catálogo en ejecución.") from error
        requested = {
            value.strip().upper()
            for value in options["connectors"].split(",")
            if value.strip()
        }
        unknown = requested - set(SCHEDULED_CONNECTORS)
        if unknown:
            raise CommandError(
                f"Conectores no programables: {', '.join(sorted(unknown))}."
            )
        interval = max(options["interval_seconds"], 60)
        while True:
            self._cycle(requested=requested, force=options["force"])
            if not options["loop"]:
                return
            time.sleep(interval)

    def _cycle(self, *, requested, force):
        started_at = timezone.now()
        previous = IntegrationReadStatus.objects.filter(
            system="CATALOG", capability="connector_scheduler"
        ).first()
        IntegrationReadStatus.objects.update_or_create(
            system="CATALOG",
            capability="connector_scheduler",
            defaults={
                "status": IntegrationReadStatus.Status.PARTIAL,
                "message": "Ciclo de lecturas en curso; los snapshots anteriores permanecen disponibles.",
                "evidence_reference": "manage.py run_connector_scheduler",
                "record_count": 0,
                "observed_at": started_at,
                "last_success_at": previous.last_success_at if previous else None,
                "external_writes": 0,
                "details": {"state": "RUNNING", "cadence_hours": 6},
            },
        )
        selected = requested or set(SCHEDULED_CONNECTORS)
        completed = []
        skipped = []
        failures = []
        for code, connector in SCHEDULED_CONNECTORS.items():
            if code not in selected:
                continue
            if not force and not scheduled_connector_due(code, now=started_at):
                skipped.append(code)
                continue
            IntegrationReadStatus.objects.filter(
                system="CATALOG", capability="connector_scheduler"
            ).update(
                message=f"Leyendo {code}; los snapshots anteriores permanecen disponibles.",
                observed_at=timezone.now(),
                record_count=len(completed),
                details={
                    "state": "RUNNING",
                    "current_connector": code,
                    "completed": completed,
                    "skipped": skipped,
                    "cadence_hours": 6,
                },
            )
            try:
                completed_process = subprocess.run(
                    [sys.executable, "manage.py", connector["command"]],
                    cwd=settings.BASE_DIR,
                    text=True,
                    capture_output=True,
                    timeout=connector["timeout_seconds"],
                    check=False,
                )
                if completed_process.returncode != 0:
                    raise RuntimeError(
                        _safe_message(completed_process.stderr)
                        or f"{connector['command']} terminó con error."
                    )
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                failures.append({"code": code, "error": _safe_message(error)})
            else:
                completed.append(code)

        finished_at = timezone.now()
        status = (
            IntegrationReadStatus.Status.PARTIAL
            if failures
            else IntegrationReadStatus.Status.AVAILABLE
        )
        previous = IntegrationReadStatus.objects.filter(
            system="CATALOG", capability="connector_scheduler"
        ).first()
        last_success = (
            finished_at
            if not failures
            else previous.last_success_at if previous else None
        )
        IntegrationReadStatus.objects.update_or_create(
            system="CATALOG",
            capability="connector_scheduler",
            defaults={
                "status": status,
                "message": (
                    f"Ciclo completado: {len(completed)} lecturas, "
                    f"{len(skipped)} todavía vigentes, {len(failures)} fallidas."
                ),
                "evidence_reference": "manage.py run_connector_scheduler",
                "record_count": len(completed),
                "observed_at": finished_at,
                "last_success_at": last_success,
                "external_writes": 0,
                "details": {
                    "completed": completed,
                    "skipped": skipped,
                    "failures": failures,
                    "cadence_hours": 6,
                    "loop_interval_seconds": 300,
                },
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Planificador: {len(completed)} lecturas, {len(skipped)} vigentes, "
                f"{len(failures)} fallidas; externalWrites=0."
            )
        )
