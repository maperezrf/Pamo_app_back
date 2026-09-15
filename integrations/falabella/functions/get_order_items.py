from ..client import FalabellaClient

VERSION = "1.0"


def get_order_items(order_id):
    """Trae los ítems de una orden de Falabella, agrupados por SKU.

    Falabella no manda un campo de cantidad: cada unidad física llega
    como una fila de `OrderItem` separada (mismo SKU, distinto
    `OrderItemId`). Esta función cuenta las filas por SKU para obtener
    la cantidad -- verificado contra una orden real el 2026-09-11.

    `Sku` (no `ShopSku`) es el SKU que usamos en Shopify -- confirmado
    contra una orden real, no asumido.

    Devuelve una lista de {"sku": str, "shop_sku": str, "quantity": int,
    "price": str, "status": str}. `price` toma `ItemPrice` (precio de
    lista, antes de descuentos/vouchers); si hace falta reflejar el precio
    con descuento aplicado, cambiar acá a `PaidPrice`. `shop_sku` se
    conserva por si hace falta más adelante, aunque `sku` es el que ya
    confirmamos que coincide con Shopify.
    """
    response = FalabellaClient().request("GetOrderItems", VERSION, {"OrderId": str(order_id)})
    raw_items = _extract_order_items(response)

    grouped = {}
    for item in raw_items:
        sku = item["Sku"]
        shop_sku = item["ShopSku"]
        entry = grouped.setdefault(
            sku,
            {
                "sku": sku,
                "shop_sku": shop_sku,
                "quantity": 0,
                "price": (item.get("ItemPrice") or "").replace(",", ""),
                "status": item.get("Status", ""),
            },
        )
        entry["quantity"] += 1
    return list(grouped.values())


def _extract_order_items(response):
    order_items = (
        (response.get("SuccessResponse") or {})
        .get("Body", {})
        .get("OrderItems", {})
        .get("OrderItem", [])
    )
    return [order_items] if isinstance(order_items, dict) else order_items
