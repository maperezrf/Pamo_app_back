import json

from config.constants import MADECENTRO_ORDERS_TOKEN

from ..client import MadecentroClient


def _format_date(value):
    # Shipturtle filtra con fechas M/D/YYYY sin ceros a la izquierda.
    return f"{value.month}/{value.day}/{value.year}"


def list_orders(start_date, end_date, *, page=1, limit=50, type_of_order="forward order"):
    """Una página de pedidos de Madecentro entre dos fechas (`date`),
    del más reciente al más antiguo. Devuelve el JSON tal cual: la
    normalización se define cuando se conozcan los campos reales."""
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
    return MadecentroClient(MADECENTRO_ORDERS_TOKEN).get("/all-orders/fetchData", params=params)
