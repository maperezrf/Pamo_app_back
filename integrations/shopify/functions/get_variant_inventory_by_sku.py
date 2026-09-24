from ..client import ShopifyClient
from ..queries import GET_VARIANT_BY_SKU


def get_variant_inventory_by_sku(sku):
    """Busca en Shopify la variante cuyo SKU es exactamente `sku` y devuelve
    su id junto con las unidades disponibles por bodega (`Location`).

    Misma verificación de SKU exacto que `get_variant_by_sku` (Shopify hace
    coincidencia parcial en `query: "sku:..."`).

    Devuelve:
        {"variant_id": str, "sku": str, "tracked": bool,
         "locations": [{"location_id": str, "name": str, "available": int}]}
    con ids sin el prefijo `gid://shopify/...`, o `None` si no hay una
    coincidencia exacta.

    `locations` solo trae las bodegas donde el ítem está dado de alta en
    Shopify; una bodega ausente equivale a 0 unidades. Solo normaliza: no
    decide de qué bodega se despacha (eso es regla de negocio de quien la
    llama).
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

    inventory_item = node.get("inventoryItem") or {}
    level_edges = (inventory_item.get("inventoryLevels") or {}).get("edges", [])
    return {
        "variant_id": node["id"].removeprefix("gid://shopify/ProductVariant/"),
        "sku": node["sku"],
        "tracked": bool(inventory_item.get("tracked")),
        "locations": [_normalize_level(edge["node"]) for edge in level_edges],
    }


def _normalize_level(level):
    location = level.get("location") or {}
    available = next(
        (entry["quantity"] for entry in level.get("quantities") or [] if entry.get("name") == "available"),
        0,
    )
    return {
        "location_id": (location.get("id") or "").removeprefix("gid://shopify/Location/"),
        "name": location.get("name", ""),
        "available": available or 0,
    }
