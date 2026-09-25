from config.constants import MADECENTRO_ORDERS_TOKEN

from ..client import MadecentroClient


def get_order(order_id):
    """Pedido de Madecentro por id interno de Shipturtle. Devuelve el JSON
    tal cual: la normalización se define cuando se conozcan los campos
    reales (ver docs/implementations-plans/madecentro-orders-import.md)."""
    return MadecentroClient(MADECENTRO_ORDERS_TOKEN).get(f"/orders/{order_id}")
