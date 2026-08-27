import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalogo.channel_import import ChannelImportError, import_external_channel_snapshot


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"


class Command(BaseCommand):
    help = "Actualiza el snapshot local Mercado Libre usando el OAuth cifrado de Pamo Maestro en Railway."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=os.getenv("PAMO_MERCADOLIBRE_RAILWAY_PROJECT", DEFAULT_PROJECT))
        parser.add_argument("--environment", default=os.getenv("PAMO_MERCADOLIBRE_RAILWAY_ENVIRONMENT", DEFAULT_ENVIRONMENT))
        parser.add_argument("--service", default=os.getenv("PAMO_MERCADOLIBRE_RAILWAY_SERVICE", DEFAULT_SERVICE))
        parser.add_argument("--timeout", type=int, default=300)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La actualización Mercado Libre solo puede persistir en SQLite local.")

        script_path = Path(settings.BASE_DIR) / "catalogo" / "scripts" / "export_mercadolibre_snapshot.mjs"
        if not script_path.is_file():
            raise CommandError("No existe el extractor local de Mercado Libre.")

        command = [
            "railway", "ssh",
            "--project", options["project"],
            "--environment", options["environment"],
            "--service", options["service"],
            "--", "node", "--input-type=module",
        ]
        environment = os.environ.copy()
        environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        environment["RAILWAY_AGENT_SESSION"] = "railway-skill-mercadolibre-local-refresh"
        environment["MERCADOLIBRE_REFRESH_SHIPPING"] = "true"
        environment["MERCADOLIBRE_REFRESH_SELLING_FEES"] = "true"

        try:
            completed = subprocess.run(
                command,
                input=script_path.read_text(encoding="utf-8"),
                text=True,
                capture_output=True,
                timeout=max(options["timeout"], 30),
                check=False,
                env=environment,
            )
        except FileNotFoundError as error:
            raise CommandError("Railway CLI no está instalado.") from error
        except subprocess.TimeoutExpired as error:
            raise CommandError("La lectura Mercado Libre agotó el tiempo; el snapshot anterior permanece intacto.") from error

        for line in completed.stderr.splitlines():
            if line.startswith("Mercado Libre"):
                self.stderr.write(line)
        if completed.returncode != 0:
            safe_tail = " | ".join(completed.stderr.splitlines()[-5:])[:1000]
            raise CommandError(f"Falló la lectura remota; no se modificó SQLite. {safe_tail}")

        try:
            payload = json.loads(completed.stdout)
            summary = import_external_channel_snapshot(
                payload.get("channel"),
                payload.get("records"),
                observed_at=payload.get("observed_at"),
                complete=payload.get("complete") is True,
                source=payload.get("source") or "Mercado Libre API read-only",
            )
        except (json.JSONDecodeError, ChannelImportError) as error:
            raise CommandError(f"La lectura remota no entregó un snapshot válido: {error}") from error

        self.stdout.write(self.style.SUCCESS(
            "Mercado Libre → SQLite local: "
            f"{summary['total']} registros, {summary['exact']} SKU exactos, "
            f"{summary['missing_shopify']} ausentes, {summary['duplicates']} duplicados; externalWrites=0."
        ))
