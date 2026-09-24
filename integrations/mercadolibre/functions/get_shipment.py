from ..client import MercadoLibreClient

# Envío despachado por Mercado Libre desde su propia bodega (Full).
LOGISTIC_TYPE_FULFILLMENT = "fulfillment"


def get_shipment(shipment_id):
    """Trae un envío de Mercado Libre (`GET /shipments/{id}`), normalizado.

    Devuelve:
        {"shipment_id", "status", "logistic_type",
         "receiver": {"name", "phone", "address", "city", "region"},
         "raw"}

    **Sin verificar contra un envío real** (levantamiento de la fase 0,
    ver docs/implementations-plans/mercadolibre-orders-import.md): se lee
    el formato clásico (`logistic_type`, `receiver_address`). Si la cuenta
    solo responde el formato nuevo (header `x-format-new: true`, con
    `logistic.type` y la dirección en otra ruta), se ajusta acá. Si el
    teléfono del destinatario viene enmascarado, queda tal cual llega.

    `raw` es el JSON crudo, solo mientras dura el levantamiento de datos.
    """
    raw = MercadoLibreClient().get(f"/shipments/{shipment_id}")
    receiver = raw.get("receiver_address") or {}
    return {
        "shipment_id": _text(raw.get("id")),
        "status": _text(raw.get("status")),
        "logistic_type": _text(raw.get("logistic_type") or (raw.get("logistic") or {}).get("type")),
        "receiver": {
            "name": _text(receiver.get("receiver_name")),
            "phone": _text(receiver.get("receiver_phone")),
            "address": _text(receiver.get("address_line")),
            "city": _text((receiver.get("city") or {}).get("name")),
            "region": _text((receiver.get("state") or {}).get("name")),
        },
        "raw": raw,
    }


def _text(value):
    return "" if value is None else str(value).strip()
