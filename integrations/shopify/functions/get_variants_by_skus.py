from ..client import ShopifyClient
from ..queries import GET_VARIANTS_BY_SKUS

# SKU por búsqueda. Valor inicial: confirmar contra la tienda real el largo
# máximo de `query` antes de subirlo.
BATCH_SIZE = 50
# Tope de páginas por bloque: la coincidencia parcial de Shopify puede traer
# muchas variantes de más, pero no infinitas.
MAX_PAGES = 20


def get_variants_by_skus(skus):
    """Busca en Shopify, por lotes, las variantes cuyos SKU son exactamente
    los de `skus`. Devuelve `{sku: variant_id}` (id sin el prefijo
    `gid://shopify/...`); un SKU sin coincidencia exacta no aparece.

    Divide los SKU únicos en bloques de `BATCH_SIZE` y arma
    `sku:"A" OR sku:"B"`. Shopify hace coincidencia parcial, así que se
    recorren todas las páginas del bloque y solo se acepta
    `node.sku == sku`, la misma regla que `get_variant_by_sku`.

    Un error de un bloque (`ShopifyGraphQLError` o HTTP) se propaga: quien
    llama decide si reintenta. Para avanzar bloque por bloque (progreso,
    reintentos), llamar con un bloque a la vez.
    """
    unique = list(dict.fromkeys(sku for sku in skus if sku))
    found = {}
    client = ShopifyClient()
    for start in range(0, len(unique), BATCH_SIZE):
        block = unique[start : start + BATCH_SIZE]
        found.update(_search_block(client, block))
    return found


def _search_block(client, block):
    wanted = set(block)
    query = " OR ".join(f'sku:"{_escape(sku)}"' for sku in block)
    found = {}
    cursor = None
    for _ in range(MAX_PAGES):
        response = client.request_graphql(GET_VARIANTS_BY_SKUS, variables={"query": query, "cursor": cursor})
        variants = (response.get("data") or {}).get("productVariants") or {}
        for edge in variants.get("edges") or []:
            node = edge["node"]
            if node.get("sku") in wanted:
                found[node["sku"]] = node["id"].removeprefix("gid://shopify/ProductVariant/")
        page_info = variants.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return found


def _escape(sku):
    return sku.replace("\\", "\\\\").replace('"', '\\"')
