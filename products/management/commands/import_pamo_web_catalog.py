import csv
import json

from django.core.management.base import BaseCommand, CommandError

from integrations.shopify.functions.get_variant_by_sku import get_variant_by_sku
from products.functions.import_equivalences import import_equivalences
from products.functions.import_kits import import_kits
from products.functions.normalize_cell import normalize_cell
from products.functions.resolve_marketplace_sku import resolve_marketplace_sku
from products.models import Marketplace, Product

SODIMAC = Marketplace.SODIMAC.value
PRODUCTS_COLUMNS = {"sku_sodimac", "sku_pamo", "ean"}
KITS_COLUMNS = {"kitnumber", "sku", "quantity", "ean"}


class Command(BaseCommand):
    help = (
        "Importación única del catálogo de Sodimac desde pamo_web: recibe los "
        "Excel que descarga pamo_web (productos_sodimac y kits_sodimac) "
        "guardados como CSV. Se puede repetir sin duplicar. Consulta Shopify "
        "(solo lectura) para descartar kits que no funcionarían. Ver "
        "docs/implementations-plans/products-catalog.md."
    )

    def add_arguments(self, parser):
        parser.add_argument("--products", help="CSV de productos_sodimac (sku_sodimac, sku_pamo, ean).")
        parser.add_argument("--kits", help="CSV de kits_sodimac (kitnumber, ean, sku, quantity).")

    def handle(self, *args, **options):
        if not options["products"] and not options["kits"]:
            raise CommandError("Indicar --products, --kits o ambos.")

        # Reglas de limpieza de los datos de pamo_web (decididas por el
        # usuario el 2026-09-25): ver "Importación desde pamo_web" en
        # docs/implementations-plans/products-catalog.md.
        if options["products"]:
            rows = _read_csv(options["products"], PRODUCTS_COLUMNS)
            equivalence_rows, skipped, inverted = _products_to_equivalences(rows)
            self._report("Productos sin sku_pamo (omitidos)", skipped)
            self._report("Filas invertidas sku_sodimac/sku_pamo (omitidas)", inverted)
            self._report("Equivalencias de productos", import_equivalences(equivalence_rows))

        if options["kits"]:
            rows = _read_csv(options["kits"], KITS_COLUMNS)
            kit_rows, kit_equivalences = _kits_to_rows(rows)
            kit_rows, discarded = _discard_unusable_kits(kit_rows, _ShopifySkus())
            self._report("Kits descartados", discarded)
            self._report("Kits", import_kits(kit_rows))
            # Solo los kits que sí quedaron cargados: un kit descartado o
            # rechazado no debe quedar como equivalencia.
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


def _looks_like_sodimac_sku(value):
    """Los SKU de Sodimac son números de 6 o más dígitos (`795744`,
    `3098486`); los de Pamo no siguen ese formato (`7809`, `602-5`)."""
    return value.isdigit() and len(value) >= 6


def _products_to_equivalences(rows):
    """`productos_sodimac` → filas de equivalencias.

    - Una fila sin `sku_pamo` no tiene a qué producto apuntar: se omite.
    - Fila invertida: `pamo_web` tiene pares cargados en los dos sentidos
      (`795744;7809` y `7809;795744`). Se omite la fila cuyo `sku_sodimac`
      no parece de Sodimac y cuyo `sku_pamo` sí, cuando el par correcto
      también viene en el archivo.
    """
    pairs = {(normalize_cell(row["sku_sodimac"]), normalize_cell(row["sku_pamo"])) for row in rows}
    equivalences, skipped, inverted = [], [], []
    for line, row in enumerate(rows, start=2):  # línea 1 = encabezado
        sku_sodimac = normalize_cell(row["sku_sodimac"])
        sku_pamo = normalize_cell(row["sku_pamo"])
        if not sku_pamo:
            skipped.append({"line": line, "sku_sodimac": sku_sodimac})
            continue
        if (
            (sku_pamo, sku_sodimac) in pairs
            and _looks_like_sodimac_sku(sku_pamo)
            and not _looks_like_sodimac_sku(sku_sodimac)
        ):
            inverted.append({"line": line, "sku_sodimac": sku_sodimac, "sku_pamo": sku_pamo})
            continue
        equivalences.append({
            "pamo_sku": sku_pamo,
            f"{SODIMAC}_sku": sku_sodimac,
            f"{SODIMAC}_ean": normalize_cell(row["ean"]),
        })
    return equivalences, skipped, inverted


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


class _ShopifySkus:
    """¿Existe este SKU en Shopify? Con caché: un componente se repite en
    muchos kits y cada consulta es una llamada a la API (solo lectura)."""

    def __init__(self):
        self._cache = {}

    def exists(self, sku):
        if sku not in self._cache:
            self._cache[sku] = get_variant_by_sku(sku) is not None
        return self._cache[sku]


def _discard_unusable_kits(kit_rows, shopify):
    """Descarta kits completos (nunca a medias: despachar un kit
    incompleto es peor que un error) en dos casos:

    - El SKU de Sodimac ya apunta a un producto simple que EXISTE en
      Shopify: gana el producto, como en pamo_web (ej. `390349` → `5092`).
      Si ese producto no existe en Shopify, gana el kit.
    - Algún componente no existe en Shopify: una orden de ese kit fallaría
      igual; sin kit, falla por SKU sin equivalencia y se revisa a mano.
    """
    by_kit = {}
    for row in kit_rows:
        by_kit.setdefault(row["kit_sku"], []).append(row)

    kept, discarded = [], []
    for kit_sku, rows in by_kit.items():
        product = resolve_marketplace_sku(SODIMAC, kit_sku)
        if product and not product.is_kit and shopify.exists(product.sku):
            discarded.append({"kit": kit_sku, "reason": f"gana el producto {product.sku}, que existe en Shopify"})
            continue
        missing = sorted({row["component_sku"] for row in rows if not shopify.exists(row["component_sku"])})
        if missing:
            discarded.append({"kit": kit_sku, "reason": "componentes que no existen en Shopify", "skus": missing})
            continue
        kept.extend(rows)
    return kept, discarded
