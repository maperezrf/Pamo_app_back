import csv

from django.core.management.base import BaseCommand, CommandError

from catalogo.models import ProviderConfig, SupplierCatalogItem


HEADERS = [
    "sku", "weight_kg", "length_cm", "width_cm", "height_cm",
    "warehouse_external_id", "warehouse_name", "reported_stock",
    "reserved_stock", "safety_stock", "observed_at", "freshness_minutes",
    "update_method", "evidence_reference",
]


class Command(BaseCommand):
    help = "Exporta por stdout la plantilla CSV local de faltantes físicos e inventario."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)

    def handle(self, *args, **options):
        try:
            provider = ProviderConfig.objects.get(name=options["provider"])
        except ProviderConfig.DoesNotExist as error:
            raise CommandError("Proveedor no encontrado.") from error
        writer = csv.writer(self.stdout)
        writer.writerow(HEADERS)
        for item in SupplierCatalogItem.objects.filter(provider=provider).order_by("supplier_sku"):
            dimensions = item.dimensions or {}
            writer.writerow([
                item.supplier_sku, item.weight_kg or "", dimensions.get("length_cm", ""),
                dimensions.get("width_cm", ""), dimensions.get("height_cm", ""), "", item.warehouse,
                item.inventory if item.inventory is not None else "", "", "", "", 1440, "FILE", "",
            ])
