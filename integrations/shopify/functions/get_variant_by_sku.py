from ..client import ShopifyClient
from ..queries import GET_VARIANT_BY_SKU


def get_variant_by_sku(sku):
    """Busca en Shopify la variante cuyo SKU es exactamente `sku`.

    Shopify hace coincidencia parcial en `query: "sku:..."`, así que se
    verifica el SKU exacto en la respuesta antes de confiar en ella --
    de lo contrario un SKU como "ABC-1" podría mapear por error a
    "ABC-1-OTRO".

    Devuelve el id de la variante sin el prefijo `gid://shopify/...`, o
    `None` si no hay una coincidencia exacta.
    """
    response = ShopifyClient().request_graphql(
        GET_VARIANT_BY_SKU,
        variables={"query": f"sku:{sku}"},
    )
    edges = response.get("data", {}).get("productVariants", {}).get("edges", [])
    if not edges:
        return None
    node = edges[0]["node"]
    if node.get("sku") != sku:
        return None
    return node["id"].removeprefix("gid://shopify/ProductVariant/")
