import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalogo.channel_import import ChannelImportError, import_external_channel_snapshot


class Command(BaseCommand):
    help = "Importa un snapshot sanitizado de Mercado Libre o Falabella a SQLite local."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede escribir en SQLite local.")
        try:
            payload = json.loads(sys.stdin.read())
            summary = import_external_channel_snapshot(
                payload.get("channel"),
                payload.get("records"),
                observed_at=payload.get("observed_at"),
                complete=payload.get("complete") is True,
                source=payload.get("source") or "API read-only",
            )
        except (json.JSONDecodeError, ChannelImportError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f"{payload.get('channel')} → SQLite local: {summary['total']} registros, "
            f"{summary['exact']} SKU exactos, {summary['missing_shopify']} ausentes en Shopify, "
            f"{summary['duplicates']} duplicados; externalWrites=0."
        ))
