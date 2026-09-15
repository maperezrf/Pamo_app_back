from datetime import date as date_cls

from ..client import SiigoClient

# Fijo siempre, confirmado por el usuario 2026-09-11 -- no es parámetro.
OBSERVATIONS = (
    "Para pagos por bancos a nombre de FEPRIN S.A.S. tenga en cuenta la "
    "siguiente información:\n"
    "DAVIVIENDA Cuenta de Ahorros 457600102745\n"
    "BANCOLOMBIA Cuenta de Ahorros 17400002178\n"
    "Revisa nuestras políticas de cambios y garantías: www.pamo.co."
)


class SiigoInvoiceError(Exception):
    """Siigo respondió sin error HTTP, pero sin un id de factura --
    resultado inesperado. Nunca reintentar la creación automáticamente:
    podría duplicar la factura."""

    def __init__(self, response):
        self.response = response
        super().__init__(str(response))


def create_invoice(
    *,
    document_id,
    customer,
    items,
    cost_center,
    seller,
    retention_ids,
    reteica,
    payment_id,
    payment_value,
    stamp_send,
    mail_send=False,
    date=None,
    due_date=None,
    purchase_order_number=None,
):
    """Crea (y opcionalmente timbra) una factura en Siigo.

    A diferencia del código anterior -- que solo facturaba a Sodimac con
    todo hardcodeado -- acá **todo** es parámetro explícito, sin default
    salvo lo marcado. `create_invoice` no calcula impuestos ni decide a
    quién facturar: solo arma el JSON exacto que espera Siigo con lo que
    ya le llega calculado, y lo envía.

    `customer`: dict con la forma exacta que espera Siigo (person_type,
    id_type, identification, branch_office, name, address, phones,
    contacts) -- se arma en la capa de negocio (facturación), no acá.
    `items`: lista de {"code", "quantity", "price", "discount", "taxes"},
    ya calculados por quien llama.
    `retention_ids`: lista de ids de tipo de retención aplicables.
    `reteica`: valor ya calculado de la retención de ICA.
    `stamp_send`: **sin default** -- nunca queda en `True` por accidente.
    Empezar en `False` mientras se prueba (confirmado 2026-09-11); pasa a
    `True` solo cuando se decida timbrar de verdad ante la DIAN.
    `date`/`due_date`: por defecto, hoy.

    Devuelve {"invoice_id": str, "raw": dict} si Siigo confirma la
    creación.

    Lanza `integrations.siigo.client.SiigoAPIError` si Siigo rechaza la
    solicitud (trae el detalle del error en `.body`), o
    `SiigoInvoiceError` si responde 2xx pero sin id de factura. Ninguno
    de los dos casos se reintenta acá -- una respuesta incierta (fallo de
    red) tampoco: quien llame debe verificar en Siigo antes de reintentar,
    para no duplicar la factura.
    """
    invoice_date = date or date_cls.today().isoformat()
    payload = {
        "document": {"id": document_id},
        "date": invoice_date,
        "customer": customer,
        "cost_center": cost_center,
        "seller": seller,
        "retentions": [{"id": retention_id} for retention_id in retention_ids],
        "reteica": reteica,
        "stamp": {"send": stamp_send},
        "mail": {"send": mail_send},
        "observations": OBSERVATIONS,
        "items": items,
        "payments": [
            {"id": payment_id, "value": payment_value, "due_date": due_date or invoice_date}
        ],
    }
    if purchase_order_number:
        payload["additional_fields"] = {"purchase_order": {"number": str(purchase_order_number)}}

    response = SiigoClient().request("POST", "invoices", json_body=payload)
    invoice_id = response.get("id")
    if not invoice_id:
        raise SiigoInvoiceError(response)
    return {"invoice_id": invoice_id, "raw": response}
