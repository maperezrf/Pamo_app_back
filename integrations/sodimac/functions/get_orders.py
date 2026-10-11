from decimal import Decimal

from config.constants import SODIMAC_PROVIDER_REFERENCE, SODIMAC_REPORT_URL

from ..client import SodimacClient
from ..constants import ALLOWED_ORDER_TYPES

VAT_MULTIPLIER = Decimal("1.19")

NO_DATA_MESSAGE = "No hay datos para esta sentencia."
SUCCESS_MESSAGE = "Sentencia ejecutada con éxito."


class SodimacReportError(Exception):
    """Sodimac respondió, pero con un mensaje que no es ni el de "sin
    datos" ni el de éxito esperado -- resultado inesperado, no visto
    todavía en producción."""


def get_orders(tipo_orden):
    """Trae las órdenes de compra de Sodimac para un tipo de orden, ya
    aplanadas a una fila por producto (igual que trae el reporte).

    `tipo_orden`: uno de ALLOWED_ORDER_TYPES (hoy "1" y "4", los únicos
    que se usan en producción) -- ampliar esa lista en
    integrations/sodimac/constants.py cuando haga falta otro tipo.

    Devuelve una lista de {"purchase_order", "status", "transmitted_at",
    "sku", "quantity", "cost", "cost_with_vat", "delivery"}. `delivery`
    son los datos de entrega de la OC (iguales en todas sus filas):
    {"name", "identification", "email", "phone", "address", "city",
    "region"}. Vacía si Sodimac no tiene datos para esa consulta.

    A diferencia del código anterior (que no manejaba un `Mensaje`
    distinto a los dos esperados), acá un mensaje inesperado levanta
    `SodimacReportError` en vez de devolver silenciosamente nada.
    """
    if str(tipo_orden) not in ALLOWED_ORDER_TYPES:
        raise ValueError(f"tipo_orden no soportado: {tipo_orden!r}")

    payload = {
        "ReferenciaProveedor": SODIMAC_PROVIDER_REFERENCE,
        "TipoOrden": str(tipo_orden),
    }
    response = SodimacClient().post(SODIMAC_REPORT_URL, payload)

    message = response.get("Mensaje", "")
    if message == NO_DATA_MESSAGE:
        return []
    if message != SUCCESS_MESSAGE:
        raise SodimacReportError(message)

    rows = []
    for order in response.get("Value") or []:
        delivery = _delivery(order)
        for product in order.get("PRODUCTOS") or []:
            cost = Decimal(str(product.get("COSTO_SKU", 0)))
            rows.append({
                "purchase_order": order.get("ORDEN_COMPRA"),
                "status": order.get("ESTADO_OC"),
                "transmitted_at": order.get("FECHA_TRANSMISION"),
                "sku": str(product.get("SKU", "")).strip(),
                "quantity": product.get("CANTIDAD_SKU"),
                "cost": cost,
                "cost_with_vat": cost * VAT_MULTIPLIER,
                "delivery": delivery,
            })
    return rows


def _delivery(order):
    # El nombre viene en un solo campo (CLIENTE). Los datos de entrega
    # (`*_ENTREGA`) son los del destino del despacho; los `*_FAC`, de la
    # factura.
    def text(key):
        return str(order.get(key) or "").strip()

    return {
        "name": text("CLIENTE"),
        "identification": text("CEDULA"),
        "email": text("EMAIL_CLIENTE"),
        "phone": text("TELEFONO_CLIENTE"),
        "address": text("DIRECCION_ENTREGA"),
        "city": text("CIUDAD_ENTREGA"),
        "region": text("DEPARTAMENTO_ENTREGA"),
    }
