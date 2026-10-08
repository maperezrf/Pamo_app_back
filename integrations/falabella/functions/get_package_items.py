from ..client import FalabellaClient
from .get_order_items import VERSION, _extract_order_items


def get_package_items(order_id):
    """Ítems de una orden de Falabella con su paquete y guía, uno por unidad
    (sin agrupar por SKU, a diferencia de `get_order_items`): la guía se
    pide con los `OrderItemId` del paquete (`get_shipping_document`).

    Devuelve [{"order_item_id": str, "package_id": str, "tracking_code":
    str, "status": str}]. `package_id` vacío = todavía sin paquete, y por
    tanto sin guía.
    """
    response = FalabellaClient().request("GetOrderItems", VERSION, {"OrderId": str(order_id)})
    return [
        {
            "order_item_id": str(item.get("OrderItemId") or ""),
            "package_id": str(item.get("PackageId") or ""),
            "tracking_code": str(item.get("TrackingCode") or ""),
            "status": str(item.get("Status") or ""),
        }
        for item in _extract_order_items(response)
    ]
