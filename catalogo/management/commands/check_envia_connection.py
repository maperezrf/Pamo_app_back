import fcntl
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from catalogo.models import CatalogHistoryEvent, IntegrationReadStatus


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"


CONNECTION_SCRIPT = r"""
const token = process.env.ENVIA_SHIPPING_API_TOKEN;
if (!token) {
  process.stdout.write(JSON.stringify({ok: false, code: "TOKEN_MISSING"}));
  process.exit(2);
}
const response = await fetch("https://queries.envia.com/carrier?country_code=CO", {
  method: "GET",
  headers: {accept: "application/json", authorization: `Bearer ${token}`},
});
let body = null;
try { body = await response.json(); } catch {}
const records = Array.isArray(body) ? body : (Array.isArray(body?.data) ? body.data : []);
process.stdout.write(JSON.stringify({
  ok: response.ok,
  http_status: response.status,
  carrier_count: records.length,
}));
if (!response.ok) process.exit(3);
"""


class Command(BaseCommand):
    help = "Comprueba la conexión autenticada con Envía mediante una lectura sin escrituras."

    def add_arguments(self, parser):
        parser.add_argument("--execute-read", action="store_true")
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval-seconds", type=int, default=21600)
        parser.add_argument("--project", default=DEFAULT_PROJECT)
        parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
        parser.add_argument("--service", default=DEFAULT_SERVICE)
        parser.add_argument("--timeout", type=int, default=90)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La comprobación solo puede registrar estado en SQLite local.")
        if not options["execute_read"]:
            self.stdout.write("Vista previa: use --execute-read para comprobar Envía; externalWrites=0.")
            return

        if options["loop"]:
            lock_path = Path(tempfile.gettempdir()) / "pamo-envia-connection-monitor.lock"
            self._lock_file = lock_path.open("a+")
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise CommandError("Ya existe un monitor de conexión Envía en ejecución.") from error
        interval = max(options["interval_seconds"], 300)
        while True:
            try:
                self._check_once(options)
            except CommandError as error:
                if not options["loop"]:
                    raise
                self.stderr.write(str(error))
            if not options["loop"]:
                return
            time.sleep(interval)

    def _check_once(self, options):

        command = [
            "railway", "ssh",
            "--project", options["project"],
            "--environment", options["environment"],
            "--service", options["service"],
            "--", "node", "--input-type=module",
        ]
        environment = os.environ.copy()
        environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        environment["RAILWAY_AGENT_SESSION"] = "railway-skill-envia-connection-check"
        completed = subprocess.run(
            command,
            input=CONNECTION_SCRIPT,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(options["timeout"], 30),
            env=environment,
        )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self._record(
                IntegrationReadStatus.Status.BLOCKED,
                "Envía no devolvió una comprobación sanitizada válida.",
                0,
                {"reason": "INVALID_SANITIZED_RESPONSE"},
            )
            raise CommandError("No fue posible verificar Envía; no se expusieron credenciales.") from error

        if completed.returncode != 0 or not result.get("ok"):
            http_status = result.get("http_status")
            reason = result.get("code") or (f"HTTP_{http_status}" if http_status else "CONNECTION_FAILED")
            self._record(
                IntegrationReadStatus.Status.BLOCKED,
                "La lectura autenticada de Envía falló; se conserva el último dato correcto.",
                0,
                {"reason": reason},
            )
            raise CommandError(f"Envía no quedó verificado ({reason}); externalWrites=0.")

        carrier_count = max(int(result.get("carrier_count") or 0), 0)
        self._record(
            IntegrationReadStatus.Status.AVAILABLE,
            "Conexión autenticada correcta; Envía respondió la consulta de transportadoras para Colombia.",
            carrier_count,
            {"country": "CO", "http_status": result.get("http_status")},
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Envía conectado: lectura autenticada correcta, {carrier_count} transportadoras; externalWrites=0."
            )
        )

    @staticmethod
    def _record(status, message, count, details):
        now = timezone.now()
        previous = IntegrationReadStatus.objects.filter(
            system="ENVIA", capability="shipping_api_connection",
        ).first()
        last_success_at = now if status == IntegrationReadStatus.Status.AVAILABLE else (
            previous.last_success_at if previous else None
        )
        with transaction.atomic():
            IntegrationReadStatus.objects.update_or_create(
                system="ENVIA",
                capability="shipping_api_connection",
                defaults={
                    "status": status,
                    "message": message,
                    "evidence_reference": "Envía GET /carrier?country_code=CO (authenticated read-only)",
                    "record_count": count,
                    "observed_at": now,
                    "last_success_at": last_success_at,
                    "external_writes": 0,
                    "details": details,
                },
            )
            CatalogHistoryEvent.objects.create(
                entity_type="ShippingConnector",
                entity_id="ENVIA",
                action="CONNECTION_CHECK",
                before={},
                after={
                    "status": status,
                    "message": message,
                    "record_count": count,
                    "details": details,
                    "external_writes": 0,
                },
                reversible=False,
                actor_label="envia-read-only-connection-check",
            )
