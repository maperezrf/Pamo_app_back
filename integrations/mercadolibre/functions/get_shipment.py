from ..client import MercadoLibreClient

# Envío despachado por Mercado Libre desde su propia bodega (Full).
LOGISTIC_TYPE_FULFILLMENT = "fulfillment"


def get_shipment(shipment_id):
    """Trae un envío de Mercado Libre (`GET /shipments/{id}`), normalizado.

    Devuelve:
        {"shipment_id", "status", "logistic_type",
         "items": [{"item_id", "quantity"}],
         "receiver": {"name", "phone", "address", "city", "region"}}

    Verificado contra 8 envíos reales el 2026-09-24 (formato clásico, sin
    header especial):
    - `logistic_type`: vistos `cross_docking` (colecta) y `self_service`
      (Flex); ningún `fulfillment` (Full) en la muestra. Si solo viniera el
      formato nuevo (`logistic.type`), también se lee.
    - `items` sale de `shipping_items`, que viene por **id de publicación**
      (`MCO…`), no por SKU: sirve para comprobar que las órdenes de un pack
      suman exactamente lo que lleva el envío.
    - `receiver.phone` llega **enmascarado** (`XXXXXXX`): no es un dato de
      contacto utilizable. La dirección de entrega puede no coincidir con
      la de facturación.
    """
    raw = MercadoLibreClient().get(f"/shipments/{shipment_id}")
    receiver = raw.get("receiver_address") or {}
    return {
        "shipment_id": _text(raw.get("id")),
        "status": _text(raw.get("status")),
        "logistic_type": _text(raw.get("logistic_type") or (raw.get("logistic") or {}).get("type")),
        "items": [
            {"item_id": _text(item.get("id")), "quantity": int(item.get("quantity") or 0)}
            for item in raw.get("shipping_items") or []
        ],
        "receiver": {
            "name": _text(receiver.get("receiver_name")),
            "phone": _text(receiver.get("receiver_phone")),
            "address": _text(receiver.get("address_line")),
            "city": _text((receiver.get("city") or {}).get("name")),
            "region": _text((receiver.get("state") or {}).get("name")),
        },
    }


def _text(value):
    return "" if value is None else str(value).strip()
