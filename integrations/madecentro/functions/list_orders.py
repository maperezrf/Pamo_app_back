import json

from config.constants import MADECENTRO_ORDERS_TOKEN

from ..client import MadecentroClient


def _format_date(value):
    # Shipturtle filtra con fechas M/D/YYYY sin ceros a la izquierda.
    return f"{value.month}/{value.day}/{value.year}"


def list_orders(start_date, end_date, *, page=1, limit=50, type_of_order="forward order"):
    """Una página de pedidos de Madecentro entre dos fechas (`date`,
    inclusive), del más reciente al más antiguo.

    El listado de Shipturtle es resumido (sus líneas no traen SKU ni
    precio): sirve para descubrir pedidos; el detalle se lee con
    `get_order`. Devuelve `{"orders": [{order_id, order_number,
    financial_status}], "count": total del rango}`.
    """
    query = {
        "order_date": {"startDate": _format_date(start_date), "endDate": _format_date(end_date)},
        "type_of_order": type_of_order,
    }
    params = {
        "query": json.dumps(query),
        "limit": limit,
        "ascending": 0,
        "page": page,
        "byColumn": 1,
    }
    response = MadecentroClient(MADECENTRO_ORDERS_TOKEN).get("/all-orders/fetchData", params=params)
    return {
        "orders": [
            {
                "order_id": str(order["id"]),
                "order_number": (order.get("name") or "").lstrip("#"),
                "financial_status": order.get("financial_status") or "",
            }
            for order in response.get("data") or []
        ],
        "count": int(response.get("count") or 0),
    }
