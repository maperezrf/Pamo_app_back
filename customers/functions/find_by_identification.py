from ..models import ShopifyCustomer


def find_by_identification(identification):
    """Busca en el directorio LOCAL (nunca en Shopify en vivo -- ver
    docs/implementations-plans/shopify-customers-directory.md) un cliente
    por su número de identificación (cédula/NIT), ya normalizado desde
    `company`.

    `identification` no es único a nivel de BD (dato posiblemente sucio de
    antes de este proyecto) -- si hay más de un match, devuelve el más
    reciente según `shopify_updated_at`.

    Devuelve el `ShopifyCustomer`, o `None` si no hay ninguno.
    """
    if not identification:
        return None
    return (
        ShopifyCustomer.objects.filter(identification=identification)
        .order_by("-shopify_updated_at")
        .first()
    )
