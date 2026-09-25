from django.db import transaction

from ..models import Marketplace, MarketplaceSku, Product
from .equivalence_columns import PAMO_SKU_COLUMN, ean_column, equivalence_columns, sku_column
from .normalize_cell import normalize_cell

SKU_MAX_LENGTH = MarketplaceSku._meta.get_field("sku").max_length
EAN_MAX_LENGTH = MarketplaceSku._meta.get_field("ean").max_length


class InvalidColumnsError(Exception):
    """La carga trae columnas que no existen en el formato (típicamente un
    error de tipeo en el encabezado del Excel). Se rechaza completa: una
    columna ignorada en silencio sería un dato perdido."""

    def __init__(self, columns):
        self.columns = columns
        super().__init__(f"Columnas desconocidas: {', '.join(columns)}")


def import_equivalences(rows):
    """Carga equivalencias en el formato de `export_equivalences` (una
    lista de dicts; `pamo_sku` obligatorio, cualquier subconjunto de
    columnas `<marketplace>_sku` / `<marketplace>_ean`).

    Por cada `<marketplace>_sku` con valor, crea o actualiza la equivalencia
    `(marketplace, sku)` y crea el `Product` si no existe. Si ese SKU ya
    apuntaba a otro producto, lo mueve y lo informa en `moved`. El EAN solo
    se escribe si su columna viene (vacío lo borra). Nunca borra
    equivalencias que no vienen en la carga.

    Una fila con cualquier error se omite completa y se informa; no aborta
    la carga. Todo corre en una transacción: un error inesperado no deja
    la carga a medias.

    Devuelve `{"created", "updated", "products_created", "moved", "errors"}`;
    `errors[].row` es la posición 1-based en `rows`. Lanza
    `InvalidColumnsError` si alguna fila trae una columna desconocida.
    """
    known_columns = set(equivalence_columns())
    unknown = sorted({column for row in rows for column in row} - known_columns)
    if unknown:
        raise InvalidColumnsError(unknown)

    result = {"created": 0, "updated": 0, "products_created": 0, "moved": [], "errors": []}
    # (marketplace, sku) → pamo_sku ya visto en esta misma carga.
    seen = {}

    with transaction.atomic():
        for position, row in enumerate(rows, start=1):
            entries, error = _parse_row(row, seen)
            if error:
                result["errors"].append({"row": position, "error": error})
                continue

            pamo_sku = normalize_cell(row.get(PAMO_SKU_COLUMN))
            product, product_created = Product.objects.get_or_create(sku=pamo_sku)
            result["products_created"] += int(product_created)
            for marketplace, sku, ean in entries:
                seen[(marketplace, sku)] = pamo_sku
                _upsert(product, marketplace, sku, ean, result)

    return result


def _parse_row(row, seen):
    """Valida la fila completa antes de escribir nada. Devuelve
    `(entries, error)`: `entries` es `[(marketplace, sku, ean_o_None)]`,
    con `None` cuando la columna del EAN no vino."""
    pamo_sku = normalize_cell(row.get(PAMO_SKU_COLUMN))
    if not pamo_sku:
        return None, "pamo_sku vacío."
    if len(pamo_sku) > Product._meta.get_field("sku").max_length:
        return None, f"pamo_sku '{pamo_sku}' supera el largo máximo."

    entries = []
    for marketplace in Marketplace.values:
        sku = normalize_cell(row.get(sku_column(marketplace)))
        has_ean = ean_column(marketplace) in row
        ean = normalize_cell(row.get(ean_column(marketplace))) if has_ean else None
        if not sku:
            if ean:
                return None, f"{ean_column(marketplace)} tiene valor pero {sku_column(marketplace)} está vacío."
            continue
        if len(sku) > SKU_MAX_LENGTH:
            return None, f"{sku_column(marketplace)} '{sku}' supera el largo máximo."
        if ean and len(ean) > EAN_MAX_LENGTH:
            return None, f"{ean_column(marketplace)} '{ean}' supera el largo máximo."
        previous = seen.get((marketplace, sku))
        if previous is not None and previous != pamo_sku:
            return None, (
                f"{sku_column(marketplace)} '{sku}' ya viene en esta carga para "
                f"'{previous}'; no puede apuntar también a '{pamo_sku}'."
            )
        entries.append((marketplace, sku, ean))
    return entries, None


def _upsert(product, marketplace, sku, ean, result):
    equivalence = (
        MarketplaceSku.objects.select_related("product")
        .filter(marketplace=marketplace, sku=sku)
        .first()
    )
    if equivalence is None:
        MarketplaceSku.objects.create(product=product, marketplace=marketplace, sku=sku, ean=ean or "")
        result["created"] += 1
        return

    changed = []
    if equivalence.product_id != product.id:
        result["moved"].append({
            "marketplace": marketplace,
            "sku": sku,
            "from": equivalence.product.sku,
            "to": product.sku,
        })
        equivalence.product = product
        changed.append("product")
    if ean is not None and equivalence.ean != ean:
        equivalence.ean = ean
        changed.append("ean")
    if changed:
        equivalence.save(update_fields=changed)
        result["updated"] += 1
