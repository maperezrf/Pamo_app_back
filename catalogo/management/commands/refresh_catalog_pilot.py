import json

from django.core.management.base import BaseCommand
from django.db import connection

from catalogo.pilot import build_bulk_metrics, refresh_read_statuses


class Command(BaseCommand):
    help = "Recalcula y persiste métricas del piloto local. No consulta ni escribe sistemas externos."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            self.stderr.write("Advertencia: este piloto fue diseñado para la base local aislada.")
        refresh_read_statuses()
        result = build_bulk_metrics(persist=True)
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
