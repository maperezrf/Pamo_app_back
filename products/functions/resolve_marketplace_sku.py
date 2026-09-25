from ..models import MarketplaceSku


def resolve_marketplace_sku(marketplace, sku):
    """Traduce el SKU que reporta un marketplace al `Product` de Pamo.

    Quita espacios a los lados (los reportes de Sodimac los traen, y
    pamo_web también hacía `strip`). Devuelve el `Product` -- simple o
    kit; para saber qué va a Shopify usar `expand_product` -- o `None` si
    no hay equivalencia registrada.
    """
    sku = str(sku or "").strip()
    if not sku:
        return None
    equivalence = (
        MarketplaceSku.objects.select_related("product")
        .filter(marketplace=marketplace, sku=sku)
        .first()
    )
    return equivalence.product if equivalence else None
