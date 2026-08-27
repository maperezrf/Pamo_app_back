import json
import os
import subprocess
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalogo.channel_import import ChannelImportError, import_external_channel_snapshot


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"


class Command(BaseCommand):
    help = "Actualiza Falabella en SQLite local mediante GetProducts de solo lectura."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=os.getenv("PAMO_CATALOG_RAILWAY_PROJECT", DEFAULT_PROJECT))
        parser.add_argument("--environment", default=os.getenv("PAMO_CATALOG_RAILWAY_ENVIRONMENT", DEFAULT_ENVIRONMENT))
        parser.add_argument("--service", default=os.getenv("PAMO_CATALOG_RAILWAY_SERVICE", DEFAULT_SERVICE))
        parser.add_argument("--timeout", type=int, default=600)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La actualización Falabella solo puede persistir en SQLite local.")
        command = [
            "railway", "run",
            "--project", options["project"],
            "--environment", options["environment"],
            "--service", options["service"],
            "--no-local", "--",
            sys.executable, "manage.py", "export_falabella_snapshot",
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
            raise CommandError("La lectura Falabella agotó el tiempo; el snapshot anterior permanece intacto.") from error
        if completed.returncode != 0:
            safe_tail = " | ".join(completed.stderr.splitlines()[-5:])[:1000]
            raise CommandError(f"Falló la lectura Falabella; no se modificó SQLite. {safe_tail}")
        try:
            payload = json.loads(completed.stdout)
            summary = import_external_channel_snapshot(
                payload.get("channel"), payload.get("records"),
                observed_at=payload.get("observed_at"),
                complete=payload.get("complete") is True,
                source=payload.get("source") or "Falabella GetProducts read-only",
            )
        except (json.JSONDecodeError, ChannelImportError) as error:
            raise CommandError(f"Falabella no entregó un snapshot válido: {error}") from error
        self.stdout.write(self.style.SUCCESS(
            "Falabella → SQLite local: "
            f"{summary['total']} registros, {summary['exact']} SKU exactos, "
            f"{summary['missing_shopify']} ausentes; externalWrites=0."
        ))
