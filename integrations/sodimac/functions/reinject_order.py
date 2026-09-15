from config.constants import SODIMAC_PROVIDER_REFERENCE, SODIMAC_REINJECT_URL

from ..client import SodimacClient

SUCCESS_DESCRIPTION = "TRANSACCION EXITOSA"


class SodimacReinjectionError(Exception):
    """Sodimac respondió, pero no confirmó éxito (`DESCRIPCION` distinta
    de "TRANSACCION EXITOSA") -- fallo definitivo según el propio
    proveedor, no reintentar sin revisar la orden primero."""

    def __init__(self, results):
        self.results = results
        super().__init__(str(results))


def reinject_order(purchase_order):
    """Pide a Sodimac que reprocese ("reinyecte") una orden de compra ya
    transmitida -- lo dispara un input puntual del front, una OC a la vez.

    Devuelve {"purchase_order": str, "reinjected": True} si Sodimac
    confirma éxito.

    Lanza `SodimacReinjectionError` si Sodimac responde pero no confirma
    éxito. Un fallo de red o una respuesta con forma inesperada se
    propaga tal cual (resultado incierto) -- no asumir que la reinyección
    no se aplicó ni reintentar automáticamente sin revisar en Sodimac.
    """
    response = SodimacClient().post(SODIMAC_REINJECT_URL, {
        "ReferenciaProveedor": SODIMAC_PROVIDER_REFERENCE,
        "PMG_PO_NUMBER": str(purchase_order),
    })
    results = response.get("Value") or []
    if not results or results[0].get("DESCRIPCION") != SUCCESS_DESCRIPTION:
        raise SodimacReinjectionError(results)
    return {"purchase_order": str(purchase_order), "reinjected": True}
