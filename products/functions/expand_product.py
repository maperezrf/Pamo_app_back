from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


class EmptyKitError(Exception):
    """El producto es kit pero no tiene componentes cargados: no se sabe
    qué enviar a Shopify. Se corrige cargando el kit, no reintentando."""

    def __init__(self, sku):
        self.sku = sku
        super().__init__(f"El kit {sku} no tiene componentes cargados.")


def expand_product(product, quantity, unit_price=None):
    """Devuelve lo que va a Shopify por `quantity` unidades de `product`:
    `[{"sku", "quantity"}]` con SKU de Pamo que existen en Shopify.

    Producto simple: él mismo. Kit: un elemento por componente, con
    `quantity × cantidad del componente en el kit`. No hay kits anidados,
    así que no se expande recursivamente.

    Con `unit_price` (precio de UNA unidad del producto), cada elemento
    lleva además `price` (`Decimal`, 2 decimales):
    - Producto simple: el mismo precio.
    - Kit: el precio se reparte en partes iguales por unidad de
      componente, como en pamo_web: `unit_price / Σ cantidades del kit`.
      El total se conserva salvo el redondeo a 2 decimales por línea.
    Es la única regla de reparto: la usan `orders.process_shipment` y la
    factura (`invoicing`).
    """
    price = None if unit_price in (None, "") else Decimal(str(unit_price))

    if not product.is_kit:
        line = {"sku": product.sku, "quantity": quantity}
        if price is not None:
            line["price"] = price.quantize(CENT, rounding=ROUND_HALF_UP)
        return [line]

    components = list(product.components.select_related("component").order_by("component__sku"))
    if not components:
        raise EmptyKitError(product.sku)

    lines = [{"sku": item.component.sku, "quantity": quantity * item.quantity} for item in components]
    if price is not None:
        units_per_kit = sum(item.quantity for item in components)
        component_price = (price / units_per_kit).quantize(CENT, rounding=ROUND_HALF_UP)
        for line in lines:
            line["price"] = component_price
    return lines
