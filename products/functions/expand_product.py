class EmptyKitError(Exception):
    """El producto es kit pero no tiene componentes cargados: no se sabe
    qué enviar a Shopify. Se corrige cargando el kit, no reintentando."""

    def __init__(self, sku):
        self.sku = sku
        super().__init__(f"El kit {sku} no tiene componentes cargados.")


def expand_product(product, quantity):
    """Devuelve lo que va a Shopify por `quantity` unidades de `product`:
    `[{"sku", "quantity"}]` con SKU de Pamo que existen en Shopify.

    Producto simple: él mismo. Kit: un elemento por componente, con
    `quantity × cantidad del componente en el kit`. No hay kits anidados,
    así que no se expande recursivamente.

    Repartir el precio del kit entre los componentes NO es de acá: es
    regla de la orden de cada canal.
    """
    if not product.is_kit:
        return [{"sku": product.sku, "quantity": quantity}]

    components = product.components.select_related("component").order_by("component__sku")
    lines = [
        {"sku": item.component.sku, "quantity": quantity * item.quantity}
        for item in components
    ]
    if not lines:
        raise EmptyKitError(product.sku)
    return lines
