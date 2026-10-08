from ..client import ShopifyClient
from ..queries import GET_ORDER_SHIPPING_ADDRESS


def get_order_shipping_address(order_id):
    """Dirección de envío de un pedido de Shopify (id numérico, sin
    `gid://`), o None si el pedido no tiene (ej. retiro en tienda).

    Devuelve {"first_name", "last_name", "company", "address1", "address2",
    "city", "province", "province_code", "zip", "phone"}, vacíos como "".
    """
    response = ShopifyClient().request_graphql(
        GET_ORDER_SHIPPING_ADDRESS, variables={"id": f"gid://shopify/Order/{order_id}"}
    )
    address = ((response.get("data") or {}).get("order") or {}).get("shippingAddress")
    if not address:
        return None
    keys = {
        "first_name": "firstName", "last_name": "lastName", "company": "company", "address1": "address1",
        "address2": "address2", "city": "city", "province": "province", "province_code": "provinceCode",
        "zip": "zip", "phone": "phone",
    }
    return {key: str(address.get(source) or "").strip() for key, source in keys.items()}
