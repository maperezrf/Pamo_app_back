from ..client import MercadoLibreClient


def get_billing_info(order_id):
    """Datos de facturación del comprador de un pedido
    (`GET /orders/{id}/billing_info`), normalizados.

    Devuelve:
        {"customer_identification_type", "customer_identification",
         "customer_first_name", "customer_last_name", "customer_address",
         "customer_city", "customer_region", "raw"}
    Campo ausente -> `""` (decidir qué hacer con datos incompletos no es
    responsabilidad del normalizador).

    **Sin verificar contra un pedido real** (levantamiento de la fase 0,
    ver docs/implementations-plans/mercadolibre-orders-import.md):
    - Endpoint: Mercado Libre anunció uno nuevo
      (`/orders/billing-info/{site_id}/{billing_info_id}`); si este
      responde error en la cuenta real, hay que migrar.
    - Forma esperada: `billing_info.doc_type`, `billing_info.doc_number` y
      `billing_info.additional_info` como lista de `{"type", "value"}`
      (FIRST_NAME, LAST_NAME, STREET_NAME, STREET_NUMBER, CITY_NAME,
      STATE_NAME…). Las llaves exactas se confirman con `raw`.

    `raw` es el JSON crudo, solo mientras dura el levantamiento de datos.
    """
    raw = MercadoLibreClient().get(f"/orders/{order_id}/billing_info")
    billing = raw.get("billing_info") or {}
    extra = {
        entry.get("type"): _text(entry.get("value"))
        for entry in billing.get("additional_info") or []
        if isinstance(entry, dict)
    }
    address = " ".join(part for part in (extra.get("STREET_NAME", ""), extra.get("STREET_NUMBER", "")) if part)
    return {
        "customer_identification_type": _text(billing.get("doc_type")),
        "customer_identification": _text(billing.get("doc_number")),
        "customer_first_name": extra.get("FIRST_NAME", ""),
        "customer_last_name": extra.get("LAST_NAME", ""),
        "customer_address": address,
        "customer_city": extra.get("CITY_NAME", ""),
        "customer_region": extra.get("STATE_NAME", ""),
        "raw": raw,
    }


def _text(value):
    return "" if value is None else str(value).strip()
