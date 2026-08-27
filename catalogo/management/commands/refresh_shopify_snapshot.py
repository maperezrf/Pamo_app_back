import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from catalogo.models import IntegrationReadStatus


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"


class Command(BaseCommand):
    help = "Actualiza el snapshot SQLite local desde Shopify usando variables Railway solo en memoria."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=os.getenv("PAMO_CATALOG_RAILWAY_PROJECT", DEFAULT_PROJECT))
        parser.add_argument("--environment", default=os.getenv("PAMO_CATALOG_RAILWAY_ENVIRONMENT", DEFAULT_ENVIRONMENT))
        parser.add_argument("--service", default=os.getenv("PAMO_CATALOG_RAILWAY_SERVICE", DEFAULT_SERVICE))
        parser.add_argument("--timeout", type=int, default=900)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La actualización Shopify solo puede persistir en SQLite local.")

        prior = IntegrationReadStatus.objects.filter(
            system="SHOPIFY", capability="marketplace_catalog_snapshot", last_success_at__isnull=False,
        ).first()
        updated_since = ""
        if prior:
            updated_since = (prior.last_success_at - timedelta(minutes=5)).isoformat()
        command = [
            "railway", "run",
            "--project", options["project"],
            "--environment", options["environment"],
            "--service", options["service"],
            "--no-local", "--",
            sys.executable, "manage.py", "export_shopify_snapshot",
            "--page-size", "10", "--max-variants", "50000",
        ]
        if updated_since:
            command.extend(["--updated-since", updated_since])
        environment = os.environ.copy()
        environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        environment["RAILWAY_AGENT_SESSION"] = "railway-skill-catalog-refresh-20260826"
        try:
            completed = subprocess.run(
                command,
                cwd=os.getcwd(),
                text=True,
                capture_output=True,
                timeout=max(options["timeout"], 60),
                check=False,
                env=environment,
            )
        except FileNotFoundError as error:
            raise CommandError("Railway CLI no está instalado.") from error
        except subprocess.TimeoutExpired as error:
            raise CommandError("La lectura Shopify agotó el tiempo; el snapshot anterior permanece intacto.") from error

        if completed.returncode != 0:
            safe_tail = " | ".join(completed.stderr.splitlines()[-5:])[:1000]
            raise CommandError(f"Falló la lectura Shopify; no se reemplazó el snapshot local. {safe_tail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CommandError("Shopify no entregó un snapshot JSON válido.") from error
        if payload.get("complete") is not True and payload.get("incremental") is not True:
            raise CommandError("La lectura Shopify quedó parcial; el snapshot completo anterior permanece vigente.")

        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload, ensure_ascii=False))):
            call_command("import_shopify_snapshot", stdout=output)
        self.stdout.write(output.getvalue().strip())
