from config.constants import ENVIA_FULFILLMENT_SHOP_ID

from ..client import EnviaFulfillmentAPIError, EnviaFulfillmentClient

PAGE_SIZE = 100
MAX_PAGES = 30


def find_variant_ids(skus):
    """`variantId` de Envía y stock por bodega de cada SKU de la tienda
    principal (`ENVIA_FULFILLMENT_SHOP_ID`), leyendo el inventario
    (`GET /company/{companyId}/warehouse/inventory`, paginado; el SKU es el
    mismo de Shopify y `ecartId` es el id de la variante en Shopify, verificado
    el 2026-10-08). El inventario no deja filtrar por SKU (`search.sku is not
    allowed`): se recorre hasta encontrar todos o terminar.

    Devuelve {sku: {"variant_id": int, "ecart_id": str,
    "available": {warehouse_id: int}}}; un SKU que no está, no aparece.
    """
    wanted = {str(sku).strip() for sku in skus if str(sku).strip()}
    if not ENVIA_FULFILLMENT_SHOP_ID:
        raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_SHOP_ID_MISSING")
    shop_id = int(ENVIA_FULFILLMENT_SHOP_ID)
    client = EnviaFulfillmentClient()
    found = {}
    for page in range(1, MAX_PAGES + 1):
        if not wanted - set(found):
            break
        data = client.get("/company/{company_id}/warehouse/inventory", params={"page": page, "limit": PAGE_SIZE}) or {}
        products = data.get("products") or []
        for product in products:
            sku = str(product.get("sku") or "").strip()
            if sku not in wanted or sku in found:
                continue
            shop = next((row for row in product.get("shops") or [] if row.get("shopId") == shop_id), None)
            if shop is None:
                continue
            found[sku] = {
                "variant_id": int(shop.get("variantId") or product["variantId"]),
                "ecart_id": str(shop.get("ecartId") or ""),
                "available": {
                    row.get("warehouseId"): int(row.get("available") or 0) for row in product.get("warehouses") or []
                },
            }
        if len(products) < PAGE_SIZE or page * PAGE_SIZE >= int(data.get("totalRows") or 0):
            break
    return found
