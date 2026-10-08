from config.constants import ENVIA_FULFILLMENT_SHOP_ID

from ..client import EnviaFulfillmentAPIError, EnviaFulfillmentClient


def list_shop_products():
    """Productos de la tienda conectada (`ENVIA_FULFILLMENT_SHOP_ID`) con
    sus variantes (`GET /company/{companyId}/client/shop/{shopId}/products`),
    tal como los devuelve Envía.

    POR VERIFICAR con una lectura real: el ejemplo de la colección solo
    trae el id de Shopify (`ecartId`) de cada variante, no el `variantId`
    de Envía que pide `create_order`. Antes de cruzar SKU → variantId hay
    que ver la respuesta real (y si pagina).
    """
    if not ENVIA_FULFILLMENT_SHOP_ID:
        raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_SHOP_ID_MISSING")
    return EnviaFulfillmentClient().get(
        f"/company/{{company_id}}/client/shop/{int(ENVIA_FULFILLMENT_SHOP_ID)}/products"
    )
