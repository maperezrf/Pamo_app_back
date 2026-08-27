import os
import subprocess
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"


class Command(BaseCommand):
    help = "Actualiza Siigo en SQLite local con variables Railway solo en memoria."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=os.getenv("PAMO_CATALOG_RAILWAY_PROJECT", DEFAULT_PROJECT))
        parser.add_argument("--environment", default=os.getenv("PAMO_CATALOG_RAILWAY_ENVIRONMENT", DEFAULT_ENVIRONMENT))
        parser.add_argument("--service", default=os.getenv("PAMO_CATALOG_RAILWAY_SERVICE", DEFAULT_SERVICE))
        parser.add_argument("--timeout", type=int, default=900)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La actualización Siigo solo puede persistir en SQLite local.")
        command = [
            "railway", "run",
            "--project", options["project"],
            "--environment", options["environment"],
            "--service", options["service"],
            "--no-local", "--",
            "env", "DATABASE_URL=", sys.executable, "manage.py", "import_siigo_readonly",
        ]
        environment = os.environ.copy()
        environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        environment["RAILWAY_AGENT_SESSION"] = "railway-skill-catalog-refresh-20260826"
        try:
            completed = subprocess.run(
                command, cwd=os.getcwd(), text=True, capture_output=True,
                timeout=max(options["timeout"], 60), check=False, env=environment,
            )
        except FileNotFoundError as error:
            raise CommandError("Railway CLI no está instalado.") from error
        except subprocess.TimeoutExpired as error:
            raise CommandError("La lectura Siigo agotó el tiempo; el snapshot anterior permanece intacto.") from error
        if completed.returncode != 0:
            safe_tail = " | ".join(completed.stderr.splitlines()[-5:])[:1000]
            raise CommandError(f"Falló la lectura Siigo; no se modificó SQLite. {safe_tail}")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.stdout.write(lines[-1] if lines else "Siigo actualizado en SQLite local; externalWrites=0.")
