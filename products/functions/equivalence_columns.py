from ..models import Marketplace

PAMO_SKU_COLUMN = "pamo_sku"


def sku_column(marketplace):
    return f"{marketplace}_sku"


def ean_column(marketplace):
    return f"{marketplace}_ean"


def equivalence_columns():
    """Columnas del formato de equivalencias, en orden: `pamo_sku` y, por
    cada `Marketplace`, `<marketplace>_sku` y `<marketplace>_ean`. Un
    marketplace nuevo en `Marketplace` aparece acá solo."""
    columns = [PAMO_SKU_COLUMN]
    for marketplace in Marketplace.values:
        columns += [sku_column(marketplace), ean_column(marketplace)]
    return columns
