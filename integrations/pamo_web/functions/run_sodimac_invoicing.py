from ..client import PamoWebClient

PATH = "/pamo_bots/sodimac/invoices"
# El endpoint es síncrono: reinyecta, lee la cola de Sodimac y factura en
# Siigo dentro de la misma solicitud. (conexión, lectura) en segundos.
TIMEOUT = (10, 900)


def run_sodimac_invoicing():
    """Pide a pamo_web que corra su facturación de Sodimac (sin crear
    órdenes): reinyecta sus OC sin factura, lee la cola, devuelve a la cola
    las OC que no conoce y factura las que estén en estado final.

    Devuelve el resumen JSON que responde pamo_web. Lanza
    `PamoWebAPIError` si no está configurado o responde con error. Un
    timeout se propaga tal cual: pamo_web puede haber terminado igual.
    """
    return PamoWebClient().post(PATH, timeout=TIMEOUT)
