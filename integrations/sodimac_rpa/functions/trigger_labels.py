from ..client import SodimacRpaClient


def trigger_labels(purchase_orders):
    """Pide al RPA generar la guía de estas OC en el portal de Homecenter
    (`POST /trigger`). El RPA trabaja en segundo plano: por cada OC consulta
    la bodega (`GET /api/orders/sodimac/rpa/orders/<oc>/stock-dispatch/`) y
    devuelve el resultado por webhook (`POST /api/orders/sodimac/rpa/callback/`).

    `SodimacRpaBusy` si ya hay una corrida en curso. Devuelve la respuesta
    del RPA ({"started": true, "ordenes": [...]}).
    """
    return SodimacRpaClient().post("/trigger", {"ordenes": [str(oc) for oc in purchase_orders]})
