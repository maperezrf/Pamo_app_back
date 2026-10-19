from ..models import MarketplaceSku


def delete_marketplace_sku(equivalence_id):
    """Borra UNA equivalencia de SKU de marketplace. El producto de Pamo no
    se toca. Devuelve `{"marketplace", "sku", "product"}` de lo borrado, o
    `None` si no existe."""
    equivalence = MarketplaceSku.objects.select_related("product").filter(pk=equivalence_id).first()
    if equivalence is None:
        return None
    deleted = {"marketplace": equivalence.marketplace, "sku": equivalence.sku, "product": equivalence.product.sku}
    equivalence.delete()
    return deleted
