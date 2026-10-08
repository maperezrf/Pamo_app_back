from ..client import EnviaFulfillmentClient
from .label import label_payload


def add_order_comment(order_id, *, comment, pdf=None, filename=None):
    """Comentario en una orden de Envía, con un PDF adjunto opcional
    (`POST /company/{companyId}/client/order/{orderId}/comment`).

    Es la única forma de la API de adjuntar un documento distinto de la
    guía. Operaciones carga ahí la **relación (remesa)** de la
    transportadora, aparte del rótulo, que va como guía en `shipments`
    (`create_order` / `add_order_tracking`). La colección oficial pide el
    archivo como "Base64 file dataUri", el mismo formato de la guía.

    Escritura: bloqueada sin `ENVIA_FULFILLMENT_WRITES_ENABLED`. Devuelve
    la respuesta de Envía.
    """
    payload = {"comment": comment, "file": None}
    if pdf is not None:
        payload["file"] = label_payload(pdf, filename or f"documento-{order_id}.pdf")
    return EnviaFulfillmentClient().post(f"/company/{{company_id}}/client/order/{int(order_id)}/comment", payload)
