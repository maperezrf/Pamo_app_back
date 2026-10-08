from decimal import Decimal

from config.constants import ENVIA_FULFILLMENT_SHOP_ID

from ..client import EnviaFulfillmentAPIError, EnviaFulfillmentClient
from .label import label_payload

COUNTRY_COLOMBIA = {"code": "CO", "name": "Colombia"}


class EnviaFulfillmentOrderError(EnviaFulfillmentAPIError):
    """Envía respondió sin crear la orden (`check` falso o sin `orderId`)."""


def create_order(
    *,
    identifier,
    warehouse_id,
    products,
    shipping_address,
    email,
    total,
    tracking_number="",
    label_pdf=None,
    tracking_url=None,
    currency="COP",
    ecommerce_status="paid",
):
    """Crea UNA orden en Envía Fulfillment con la guía del canal adjunta
    (`POST /company/{companyId}/client/order/create`, campo `shipments`:
    "cuando el cliente ya tiene la información de tracking, para que la
    bodega la use al despachar").

    - `identifier`: identificador propio de la orden (`orderIdentifier`).
      Quien llama comprueba antes con `find_order` que no exista.
    - `products`: [{"variant_id", "quantity", "price", "discount"?}] con el
      `variantId` **de Envía**.
    - `shipping_address`: {"first_name", "last_name", "address1",
      "address2", "district", "identification", "state_code", "state_name",
      "city", "postal_code", "phone", "company", "references"}. Colombia.
    - Sin `customerId`: Envía exige dirección + `email` en ese caso.
    - `label_pdf` + `tracking_number`: la guía del canal. Sin ellos la orden
      va sin `shipments` y la bodega gestiona la guía.

    Es una escritura: falla con `EnviaFulfillmentWritesDisabled` sin
    `ENVIA_FULFILLMENT_WRITES_ENABLED`. Si falla la red, no se sabe si la
    orden quedó creada: no reintentar sin volver a `find_order`.

    Devuelve {"order_id", "identifier", "warehouse_status"}.
    """
    if not products:
        raise ValueError("La orden necesita al menos un producto")
    if not ENVIA_FULFILLMENT_SHOP_ID:
        raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_SHOP_ID_MISSING")
    if not email:
        raise ValueError("Sin customerId, Envía exige email")
    # Envía rechaza (400) apellido o código de departamento vacíos, aunque
    # la colección no lo diga (verificado el 2026-10-08). El código es el
    # `code_2_digits` de https://queries.envia.com/state?country_code=CO
    # (ej. Cundinamarca "CN", Magdalena "MA", Bogotá "DC", Antioquia "AN").
    if not shipping_address.get("last_name"):
        raise ValueError("Envía exige apellido en la dirección de envío")
    if not shipping_address.get("state_code"):
        raise ValueError("Envía exige el código de departamento (state_code)")
    order = {
        # Obligatorio aunque la tabla de la colección no lo marque (Envía
        # respondió 400 "[0].shopId is required", 2026-10-08). Es la tienda
        # principal: los `variantId` son de esa tienda.
        "shopId": int(ENVIA_FULFILLMENT_SHOP_ID),
        "warehouseId": int(warehouse_id),
        "orderIdentifier": str(identifier),
        "ecommerceStatus": ecommerce_status,
        "total": _number(total),
        "currency": currency,
        "email": email,
        "products": [
            {
                "variantId": int(product["variant_id"]),
                "quantity": int(product["quantity"]),
                "price": _number(product["price"]),
                "discount": _number(product.get("discount", 0)),
            }
            for product in products
        ],
        "shippingAddress": _address(shipping_address),
        "shipments": [],
        "cod": False,
    }
    if label_pdf is not None:
        if not tracking_number:
            raise ValueError("Una guía adjunta necesita su número")
        order["shipments"] = [
            {
                "trackingNumber": str(tracking_number),
                "trackingUrl": tracking_url,
                "label": label_payload(label_pdf, f"guia-{identifier}.pdf"),
            }
        ]
    response = EnviaFulfillmentClient().post("/company/{company_id}/client/order/create", [order])
    created = response[0] if isinstance(response, list) and response else response
    if not isinstance(created, dict) or not created.get("check") or not created.get("orderId"):
        raise EnviaFulfillmentOrderError("ENVIA_FULFILLMENT_ORDER_NOT_CREATED", provider_message=str(created)[:500])
    return {
        "order_id": created["orderId"],
        "identifier": created.get("identifier") or str(identifier),
        "warehouse_status": created.get("warehouseStatus") or "",
    }


def _address(address):
    return {
        "firstName": address["first_name"],
        "lastName": address.get("last_name") or "",
        "address1": address["address1"],
        "address2": address.get("address2") or "",
        "district": address.get("district") or None,
        "identificationNumber": address.get("identification") or None,
        "country": COUNTRY_COLOMBIA,
        "state": {"code": address.get("state_code") or "", "name": address["state_name"]},
        "city": address["city"],
        "postalCode": address.get("postal_code") or "",
        "phone": address.get("phone") or "",
        "company": address.get("company") or None,
        "references": address.get("references") or None,
    }


def _number(value):
    # Envía espera números JSON; los importes llegan como string o Decimal.
    number = Decimal(str(value))
    return int(number) if number == number.to_integral_value() else float(number)
