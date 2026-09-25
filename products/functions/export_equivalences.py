from itertools import zip_longest

from ..models import Marketplace, Product
from .equivalence_columns import PAMO_SKU_COLUMN, ean_column, equivalence_columns, sku_column


def export_equivalences():
    """Equivalencias en formato de columnas, para que el frontend las
    descargue como Excel: `{"columns": [...], "rows": [...]}`.

    Una fila por producto (incluidos los que no tienen equivalencias). Si
    un producto tiene varios SKU en un mismo marketplace, la fila se repite
    con el mismo `pamo_sku` -- el mismo formato que acepta
    `import_equivalences`, así que descargar y volver a cargar no cambia
    nada.
    """
    rows = []
    products = Product.objects.prefetch_related("marketplace_skus").order_by("sku")
    for product in products:
        by_marketplace = {marketplace: [] for marketplace in Marketplace.values}
        for equivalence in sorted(product.marketplace_skus.all(), key=lambda e: e.sku):
            by_marketplace[equivalence.marketplace].append(equivalence)

        # Sin equivalencias: una sola fila con las columnas de marketplace vacías.
        groups = list(zip_longest(*by_marketplace.values())) or [(None,) * len(by_marketplace)]
        for group in groups:
            row = {PAMO_SKU_COLUMN: product.sku}
            for marketplace, equivalence in zip(by_marketplace, group):
                row[sku_column(marketplace)] = equivalence.sku if equivalence else ""
                row[ean_column(marketplace)] = equivalence.ean if equivalence else ""
            rows.append(row)

    return {"columns": equivalence_columns(), "rows": rows}
