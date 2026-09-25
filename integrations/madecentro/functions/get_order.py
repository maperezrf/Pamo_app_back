from config.constants import MADECENTRO_ORDERS_TOKEN

from ..client import MadecentroClient


def get_order(order_id):
    """Pedido de Madecentro por id interno de Shipturtle, normalizado.

    Shipturtle no entrega el documento (cédula/NIT) del comprador -- ver
    docs/implementations-plans/madecentro-orders-import.md. El nombre sale
    de la dirección de facturación; si viene vacío (empresa), se usa el
    nombre completo o la razón social de esa dirección.
    """
    data = MadecentroClient(MADECENTRO_ORDERS_TOKEN).get(f"/orders/{order_id}")["data"]
    billing = data.get("billing_address") or data.get("shipping_address") or {}

    first_name = billing.get("first_name") or data.get("customer_first_name") or ""
    last_name = billing.get("last_name") or data.get("customer_last_name") or ""
    if not first_name and not last_name:
        first_name = billing.get("name") or billing.get("company") or ""

    return {
        "order_id": str(data["id"]),
        "order_number": (data.get("name") or "").lstrip("#") or str(data.get("order_number") or ""),
        "financial_status": data.get("financial_status") or "",
        "cancelled": bool(data.get("cancelled_at")),
        "customer_first_name": first_name.strip(),
        "customer_last_name": last_name.strip(),
        "customer_email": data.get("customer_email") or data.get("email") or "",
        "customer_phone": billing.get("phone") or data.get("customer_phone") or "",
        "customer_address": ", ".join(
            part.strip() for part in (billing.get("address1"), billing.get("address2")) if part and part.strip()
        ),
        "customer_city": billing.get("city") or "",
        "customer_region": billing.get("province") or "",
        "items": [
            {"sku": item.get("sku") or "", "quantity": int(item["quantity"]), "price": str(item["price"])}
            for item in data.get("line_items") or []
        ],
    }
