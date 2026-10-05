from products.models import Marketplace

# Valor de `marketplace` para los pedidos de Shopify que no vienen de un
# marketplace (tienda web, venta directa, Addi, cotizaciones...).
SHOPIFY_CHANNEL = "shopify"
MARKETPLACE_FILTERS = [*Marketplace.values, SHOPIFY_CHANNEL]


def marketplace_from_tags(tags):
    """Marketplace de un pedido de Shopify por sus etiquetas, sin distinguir
    mayúsculas: `process_shipment` etiqueta con el valor de `Marketplace`
    (`sodimac`) y `pamo_web` lo hacía en mayúsculas (`SODIMAC`). Sin ninguna
    → `shopify`."""
    lowered = {tag.strip().lower() for tag in tags}
    return next((value for value in Marketplace.values if value in lowered), SHOPIFY_CHANNEL)
