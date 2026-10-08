from ..client import EnviaFulfillmentClient


def get_order(order_id):
    """Detalle de una orden de Envía
    (`GET /company/{companyId}/client/order/detail/{orderId}`), tal como lo
    devuelve Envía: incluye `items`, `shippingAddress` y `shipments`.
    Sirve para revisar una orden; las demás funciones no dependen de él.
    """
    return EnviaFulfillmentClient().get(f"/company/{{company_id}}/client/order/detail/{int(order_id)}")
