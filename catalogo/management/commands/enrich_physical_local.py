from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from catalogo.models import ProviderConfig, SupplierCatalogItem
from catalogo.physical import analyze_local_item, build_shopify_preview, select_pilot


class Command(BaseCommand):
    help = "Extrae evidencia física de descripciones/metacampos locales y selecciona piloto."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="Barú")
        parser.add_argument("--pilot-size", type=int, default=25)

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este enriquecimiento solo puede persistir en SQLite local.")
        provider = ProviderConfig.objects.get(name=options["provider"])
        items = SupplierCatalogItem.objects.filter(provider=provider).prefetch_related("reconciliations__variant__product")
        analyzed = candidates = 0
        for item in items.iterator(chunk_size=100):
            match = item.reconciliations.filter(status="EXACT", variant__isnull=False).select_related("variant__product").first()
            if not match:
                continue
            analyzed += 1
            candidates += len(analyze_local_item(item, match.variant))
        selected = select_pilot(SupplierCatalogItem.objects.filter(provider=provider), limit=max(20, min(options["pilot_size"], 30)))
        for row in selected:
            build_shopify_preview(row.variant)
        self.stdout.write(self.style.SUCCESS(
            f"Evidencia local: {analyzed} SKU analizados, {candidates} candidatos procesados, "
            f"{len(selected)} seleccionados; externalWrites=0."
        ))
