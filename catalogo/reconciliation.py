from .models import SkuReconciliation


def classify_exact_sku(supplier_item, variants):
    sku = supplier_item.supplier_sku.strip()
    if not sku:
        return SkuReconciliation.Status.MISSING, [], "El catálogo del proveedor no trae SKU."
    candidates = [variant for variant in variants if variant.sku == sku]
    supplier_duplicates = supplier_item.provider.catalog_items.filter(supplier_sku=sku).exclude(pk=supplier_item.pk).exists()
    if supplier_duplicates:
        return SkuReconciliation.Status.DUPLICATE, candidates, "El mismo SKU aparece más de una vez en el catálogo del proveedor."
    if not candidates:
        return SkuReconciliation.Status.MISSING, [], "No existe una variante local con el SKU exacto."
    if len(candidates) > 1:
        return SkuReconciliation.Status.AMBIGUOUS, candidates, "Más de una variante local usa el mismo SKU exacto."
    return SkuReconciliation.Status.EXACT, candidates, "Coincidencia por SKU exacto, sensible a mayúsculas y espacios."
