from ..client import MercadoLibreClient

SITE_ID = "MCO"  # Mercado Libre Colombia


def get_billing_info(billing_info_id):
    """Datos de facturación del comprador
    (`GET /orders/billing-info/MCO/{billing_info_id}`, header
    `x-version: 2`), normalizados. `billing_info_id` sale del pedido
    (`get_order()["billing_info_id"]`, `buyer.billing_info.id`).

    Devuelve:
        {"customer_identification_type", "customer_identification",
         "customer_first_name", "customer_last_name", "customer_address",
         "customer_city", "customer_region", "customer_type"}
    Campo ausente -> `""`.

    Verificado contra 8 pedidos reales el 2026-09-24 (requiere el permiso
    de facturación habilitado en la app de Mercado Libre; sin él responde
    403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`):
    - `identification.type` / `.number`: siempre; tipos vistos `CC`, `NIT`.
    - `name` siempre; `last_name` vacío en los 2 NIT de la muestra (ahí
      `name` sería la razón social).
    - `address.street_name` + `street_number` (unidos con espacio),
      `city_name`, `state.name`: siempre.
    - `attributes.cust_type`: `CO` en los 6 CC y `BU` en los 2 NIT
      (consumidor / empresa); se entrega tal cual en `customer_type`.
    - **No trae email ni teléfono.**
    Se prefirió a `GET /orders/{id}/billing_info` (clásico, también
    responde) porque trae dirección y tipo de cliente.
    """
    raw = MercadoLibreClient().get(f"/orders/billing-info/{SITE_ID}/{billing_info_id}", headers={"x-version": "2"})
    billing = (raw.get("buyer") or {}).get("billing_info") or {}
    identification = billing.get("identification") or {}
    address = billing.get("address") or {}
    street = " ".join(part for part in (_text(address.get("street_name")), _text(address.get("street_number"))) if part)
    return {
        "customer_identification_type": _text(identification.get("type")),
        "customer_identification": _text(identification.get("number")),
        "customer_first_name": _text(billing.get("name")),
        "customer_last_name": _text(billing.get("last_name")),
        "customer_address": street,
        "customer_city": _text(address.get("city_name")),
        "customer_region": _text((address.get("state") or {}).get("name")),
        "customer_type": _text((billing.get("attributes") or {}).get("cust_type")),
    }


def _text(value):
    return "" if value is None else str(value).strip()
