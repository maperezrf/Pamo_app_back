from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalogo.sodimac_catalog import (
    DEFAULT_HEADER_MAPPING,
    SodimacCatalogError,
    apply_sodimac_import,
    preview_sodimac_import,
)
from catalogo.sodimac_kits import SodimacKitError, import_sodimac_kits


DEFAULT_SOURCE_DIR = Path("/Users/mauricioperez/Documents/PAMO_APP/_local_sources/sodimac/2026-08-26")


class Command(BaseCommand):
    help = "Importa las relaciones SKU PAMO↔Sodimac y las recetas de kits en SQLite local."

    def add_arguments(self, parser):
        parser.add_argument("--products", type=Path, default=DEFAULT_SOURCE_DIR / "productos_sodimac.xlsx")
        parser.add_argument("--kits", type=Path, default=DEFAULT_SOURCE_DIR / "kits_sodimac.xlsx")
        parser.add_argument("--actor", default="codex-local-sodimac-import")

    def handle(self, *args, **options):
        products_path = options["products"].expanduser().resolve()
        kits_path = options["kits"].expanduser().resolve()
        if not products_path.is_file() or not kits_path.is_file():
            raise CommandError("Se requieren ambos archivos: publicaciones y kits Sodimac.")

        product_mapping = {
            **DEFAULT_HEADER_MAPPING,
            "canonical_sku": "sku_pamo",
            "sodimac_sku": "sku_sodimac",
            "listing_id": "sku_sodimac",
            "barcode": "ean",
        }
        try:
            product_batch = preview_sodimac_import(
                products_path.name,
                products_path.read_bytes(),
                product_mapping,
                options["actor"],
                allow_partial=True,
            )
            product_batch = apply_sodimac_import(product_batch.id, options["actor"])
            canonical_by_sodimac = {}
            for row in product_batch.rows.exclude(sodimac_sku="").exclude(canonical_sku=""):
                existing = canonical_by_sodimac.get(row.sodimac_sku)
                if existing and existing.casefold() != row.canonical_sku.casefold():
                    raise CommandError(f"El SKU Sodimac {row.sodimac_sku} tiene más de un dueño PAMO.")
                canonical_by_sodimac[row.sodimac_sku] = row.canonical_sku
            kit_batch = import_sodimac_kits(
                kits_path.name,
                kits_path.read_bytes(),
                canonical_by_sodimac,
                options["actor"],
            )
        except (SodimacCatalogError, SodimacKitError) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(
            "Sodimac → SQLite local: "
            f"{product_batch.applied_links} publicaciones vinculadas de {product_batch.total_rows}; "
            f"{kit_batch.kit_count} kits y {kit_batch.component_rows} componentes; "
            f"{kit_batch.resolved_kits} kits resueltos, {kit_batch.review_kits} por revisar; "
            "externalWrites=0."
        ))
