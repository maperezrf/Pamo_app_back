from django.db import transaction

from ..models import Product


class NotAKitError(Exception):
    """El producto existe pero no es kit: un producto simple no se borra
    desde aquí."""


def delete_kit(product_id):
    """Borra un kit del catálogo: el `Product` con `is_kit=True`, sus
    componentes y sus equivalencias de marketplace (en cascada). Los
    productos que eran sus componentes no se tocan. Nada fuera de
    `products` apunta a `Product`.

    Devuelve `{"sku", "components", "marketplace_skus"}` (conteos de lo
    borrado), o `None` si no existe. Lanza `NotAKitError` si no es kit."""
    with transaction.atomic():
        kit = Product.objects.select_for_update().filter(pk=product_id).first()
        if kit is None:
            return None
        if not kit.is_kit:
            raise NotAKitError(f"'{kit.sku}' no es un kit.")
        deleted = {
            "sku": kit.sku,
            "components": kit.components.count(),
            "marketplace_skus": kit.marketplace_skus.count(),
        }
        kit.delete()
    return deleted
