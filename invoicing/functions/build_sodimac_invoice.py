from decimal import ROUND_HALF_UP, Decimal

from products.functions.expand_product import expand_product
from products.functions.resolve_marketplace_sku import resolve_marketplace_sku

from ..constants import (
    IVA_RATE,
    RETEFUENTE_RATE,
    RETEICA_RATE,
    RETEIVA_RATE,
    SODIMAC_ITEM_TAXES,
)

CENT = Decimal("0.01")


def build_sodimac_invoice(order, calculated_total=None):
    """Líneas y totales de la factura de una OC de Sodimac, con las reglas
    de pamo_web (SigoConnection.get_data / create_invoice).

    Cada `MarketplaceOrderItem` (precio = COSTO_SKU sin IVA) se traduce
    con el catálogo de `products`: un SKU sin equivalencia va tal cual,
    un producto simple con su SKU de Pamo y un kit en una línea por
    componente con el precio repartido (`expand_product`).

    Totales, sobre los precios ya redondeados de las líneas (los mismos
    que suma Siigo):
    - iva = subtotal × 0.19; reteiva = iva × 0.15;
      reteica = subtotal × 0.01104; retefuente = subtotal × 0.025;
    - total = subtotal + iva − reteiva − reteica − retefuente, cada término
      redondeado a 2 decimales.
    `calculated_total` (el total que Siigo dijo calcular al rechazar una
    factura anterior) reemplaza al total, como en pamo_web.

    Devuelve un dict con `items` (listos para Siigo, con números `float`)
    y `subtotal`, `iva`, `reteiva`, `reteica`, `retefuente`, `total`
    (`Decimal` de 2 decimales).
    """
    items = []
    subtotal = Decimal("0")
    for item in order.items.all().order_by("pk"):
        for line in _invoice_lines(order.marketplace, item):
            items.append(
                {
                    "code": line["sku"],
                    "quantity": line["quantity"],
                    "price": float(line["price"]),
                    "discount": 0,
                    "taxes": [dict(tax) for tax in SODIMAC_ITEM_TAXES],
                }
            )
            subtotal += line["price"] * line["quantity"]

    iva = _cents(subtotal * IVA_RATE)
    reteiva = _cents(subtotal * IVA_RATE * RETEIVA_RATE)
    reteica = _cents(subtotal * RETEICA_RATE)
    retefuente = _cents(subtotal * RETEFUENTE_RATE)
    subtotal = _cents(subtotal)
    total = _cents(subtotal + iva - reteiva - reteica - retefuente)
    if calculated_total is not None:
        total = _cents(Decimal(str(calculated_total)))
    return {
        "items": items,
        "subtotal": subtotal,
        "iva": iva,
        "reteiva": reteiva,
        "reteica": reteica,
        "retefuente": retefuente,
        "total": total,
    }


def _invoice_lines(marketplace, item):
    product = resolve_marketplace_sku(marketplace, item.marketplace_sku)
    if product is None:
        price = _cents(Decimal(str(item.unit_price or "0")))
        return [{"sku": item.marketplace_sku.strip(), "quantity": item.quantity, "price": price}]
    return expand_product(product, item.quantity, item.unit_price or "0")


def _cents(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
