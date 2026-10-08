from ..client import ShopifyClient
from ..queries import LIST_LOCATIONS


def list_locations():
    """Bodegas (ubicaciones) de la tienda, también las inactivas.

    Devuelve [{"location_id": str, "name": str, "is_active": bool,
    "city": str}], con el id sin el prefijo `gid://shopify/Location/`.
    Solo normaliza: qué se hace con cada bodega lo decide quien llama.
    """
    response = ShopifyClient().request_graphql(LIST_LOCATIONS)
    nodes = (((response.get("data") or {}).get("locations") or {}).get("nodes")) or []
    return [
        {
            "location_id": node["id"].removeprefix("gid://shopify/Location/"),
            "name": node.get("name") or "",
            "is_active": bool(node.get("isActive")),
            "city": (node.get("address") or {}).get("city") or "",
        }
        for node in nodes
    ]
