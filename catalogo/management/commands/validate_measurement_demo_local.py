from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalogo.envia_quote import EnviaQuoteContractError, validate_quote_request
from catalogo.physical_measurements import apply_measurement_import, preview_measurement_import


class Command(BaseCommand):
    help = "Valida el flujo DEMO de empaque sin crear evidencia comercial ni llamadas externas."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este DEMO solo puede ejecutarse en SQLite local.")
        path = Path(__file__).resolve().parents[2] / "fixtures" / "phase5_demo_package_measurements.csv"
        batch = preview_measurement_import("Barú", path.name, path.read_bytes())
        apply_measurement_import(batch.id, actor_label="phase5-demo-qa")
        try:
            validate_quote_request({
                "destination": {"city": "Bogotá", "state": "Bogotá D.C.", "country": "CO"},
                "package": {"length": 50, "width": 50, "height": 25, "weight": 9.9, "scope": "PACKAGE", "evidence_classification": "DEMO_NO_CONFIRMADO"},
            })
        except EnviaQuoteContractError:
            self.stdout.write(self.style.SUCCESS(
                f"DEMO bloqueado correctamente: lote={batch.id}, filas={batch.total_rows}, "
                "candidatos_confirmados=0, cotizacion_envia=BLOCKED, externalWrites=0."
            ))
            return
        raise CommandError("El DEMO atravesó una compuerta que debía bloquearlo.")
