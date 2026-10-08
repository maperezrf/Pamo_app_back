from ..client import EnviaFulfillmentClient
from .label import label_payload


def add_order_tracking(order_id, *, tracking_number, label_pdf, tracking_url=None, label_name=None):
    """Agrega la guía a una orden ya creada, antes de que se despache
    (`POST /company/{companyId}/client/order/{orderId}/tracking-numbers`).
    Para cuando la orden se creó sin guía o hay que reemplazarla.

    Escritura: bloqueada sin `ENVIA_FULFILLMENT_WRITES_ENABLED`. Devuelve
    la respuesta de Envía.
    """
    return EnviaFulfillmentClient().post(
        f"/company/{{company_id}}/client/order/{int(order_id)}/tracking-numbers",
        [
            {
                "trackingNumber": str(tracking_number),
                "trackingUrl": tracking_url,
                "label": label_payload(label_pdf, label_name or f"guia-{order_id}.pdf"),
            }
        ],
    )
