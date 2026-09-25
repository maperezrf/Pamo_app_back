import csv
import json

from django.core.management.base import BaseCommand, CommandError

from products.functions.import_equivalences import import_equivalences
from products.functions.import_kits import import_kits
from products.functions.normalize_cell import normalize_cell
from products.models import Marketplace, Product

SODIMAC = Marketplace.SODIMAC.value
PRODUCTS_COLUMNS = {"sku_sodimac", "sku_pamo", "ean"}
KITS_COLUMNS = {"kitnumber", "sku", "quantity", "ean"}


class Command(BaseCommand):
    help = (
        "Importación única del catálogo de Sodimac desde pamo_web: recibe los "
        "Excel que descarga pamo_web (productos_sodimac y kits_sodimac) "
        "guardados como CSV. Se puede repetir sin duplicar. Ver "
        "docs/implementations-plans/products-catalog.md."
    )

    def add_arguments(self, parser):
        parser.add_argument("--products", help="CSV de productos_sodimac (sku_sodimac, sku_pamo, ean).")
        parser.add_argument("--kits", help="CSV de kits_sodimac (kitnumber, ean, sku, quantity).")

    def handle(self, *args, **options):
        if not options["products"] and not options["kits"]:
            raise CommandError("Indicar --products, --kits o ambos.")

        # Productos primero: si un kitnumber también figura como sku_sodimac,
        # la equivalencia del kit (cargada después) gana, igual que en
        # pamo_web, donde set_kits corre después de make_merge.
        if options["products"]:
            rows = _read_csv(options["products"], PRODUCTS_COLUMNS)
            equivalence_rows, skipped = _products_to_equivalences(rows)
            self._report("Productos sin sku_pamo (omitidos)", skipped)
            self._report("Equivalencias de productos", import_equivalences(equivalence_rows))

        if options["kits"]:
            rows = _read_csv(options["kits"], KITS_COLUMNS)
            kit_rows, kit_equivalences = _kits_to_rows(rows)
            self._report("Kits", import_kits(kit_rows))
            # Solo los kits que sí quedaron cargados: un kit rechazado no
            # debe quedar como equivalencia hacia un producto simple.
            loaded = set(Product.objects.filter(is_kit=True).values_list("sku", flat=True))
            kit_equivalences = [row for row in kit_equivalences if row["pamo_sku"] in loaded]
            self._report("Equivalencias de kits", import_equivalences(kit_equivalences))

    def _report(self, title, data):
        self.stdout.write(self.style.MIGRATE_HEADING(title))
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))


def _read_csv(path, required_columns):
    """Lee un CSV exportado desde Excel. Detecta `,` o `;` (Excel en
    español guarda con `;`) y tolera el BOM de UTF-8."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as file:
            sample = file.read(4096)
            file.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            rows = list(csv.DictReader(file, dialect=dialect))
    except OSError as error:
        raise CommandError(f"No se pudo leer {path}: {error}")
    except csv.Error as error:
        raise CommandError(f"{path} no parece un CSV válido: {error}")

    columns = {column.strip() for column in (rows[0].keys() if rows else [])}
    missing = required_columns - columns
    if missing:
        raise CommandError(f"A {path} le faltan columnas: {', '.join(sorted(missing))}")
    return [{key.strip(): value for key, value in row.items()} for row in rows]


def _products_to_equivalences(rows):
    """`productos_sodimac` → filas de equivalencias. Una fila sin `sku_pamo`
    no tiene a qué producto apuntar: se omite y se informa."""
    equivalences, skipped = [], []
    for line, row in enumerate(rows, start=2):  # línea 1 = encabezado
        sku_pamo = normalize_cell(row["sku_pamo"])
        if not sku_pamo:
            skipped.append({"line": line, "sku_sodimac": normalize_cell(row["sku_sodimac"])})
            continue
        equivalences.append({
            "pamo_sku": sku_pamo,
            f"{SODIMAC}_sku": normalize_cell(row["sku_sodimac"]),
            f"{SODIMAC}_ean": normalize_cell(row["ean"]),
        })
    return equivalences, skipped


def _kits_to_rows(rows):
    """`kits_sodimac` → filas de kits + la equivalencia de Sodimac de cada
    kit. `kitnumber` es el SKU que usa Sodimac; en pamo_web el kit no tiene
    otro nombre, así que también es su SKU de Pamo."""
    kit_rows = []
    kit_equivalences = {}
    for row in rows:
        kitnumber = normalize_cell(row["kitnumber"])
        kit_rows.append({
            "kit_sku": kitnumber,
            "component_sku": normalize_cell(row["sku"]),
            "quantity": row["quantity"],
        })
        ean = normalize_cell(row["ean"])
        if kitnumber and (kitnumber not in kit_equivalences or ean):
            kit_equivalences[kitnumber] = {
                "pamo_sku": kitnumber,
                f"{SODIMAC}_sku": kitnumber,
                f"{SODIMAC}_ean": ean,
            }
    return kit_rows, list(kit_equivalences.values())
