"""Formato común de las dos listas de `GET /api/orders/` (pedidos de
Shopify y pedidos no creados). Ver
docs/implementations-plans/shopify-orders-listing.md."""

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

COLOMBIA_TZ = ZoneInfo("America/Bogota")
CENTS = Decimal("0.01")


def to_money(value):
    """String con dos decimales, o `None` si `value` no es un importe."""
    try:
        return Decimal(str(value).strip()).quantize(CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def money_str(amount):
    return "" if amount is None else str(amount)


def format_line(line, unit_price, quantity):
    """Agrega a `line` `quantity`, `unit_price` y `line_total = unit_price ×
    quantity`. Un precio que no es importe deja los dos en `""`. Devuelve
    (línea, total de la línea o None)."""
    price = to_money(unit_price)
    line_total = None if price is None else (price * quantity).quantize(CENTS)
    line.update(quantity=quantity, unit_price=money_str(price), line_total=money_str(line_total))
    return line, line_total


def sum_lines(line_totals):
    """Total de la venta: suma de las líneas con importe."""
    return sum((total for total in line_totals if total is not None), Decimal("0.00"))


def buyer_from_row(row):
    """Comprador real guardado en `MarketplaceOrder`."""
    return {
        "identification_type": row.customer_identification_type,
        "identification": row.customer_identification,
        "first_name": row.customer_first_name,
        "last_name": row.customer_last_name,
        "email": row.customer_email,
        "phone": row.customer_phone,
        "city": row.customer_city,
        "region": row.customer_region,
        "address": row.customer_address,
    }


def fulfillment_from_row(row):
    """Bodega de despacho tal cual en `MarketplaceOrder`. Con `novedad`, la
    bodega queda vacía y `note` trae el motivo. `row=None` (pedido sin fila
    local, ej. tienda web) → todo vacío: este sistema no lo evaluó."""
    if row is None:
        return {"status": "", "location_id": "", "location_name": "", "note": ""}
    return {
        "status": row.fulfillment_status,
        "location_id": row.fulfillment_location_id,
        "location_name": row.fulfillment_location_name,
        "note": row.fulfillment_note,
    }


def day_range(date_from=None, date_to=None):
    """Fechas (`date`, inclusive) → (desde, hasta) como datetimes de
    Colombia; `hasta` es el inicio del día siguiente (exclusivo)."""
    start = datetime.combine(date_from, time.min, tzinfo=COLOMBIA_TZ) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=COLOMBIA_TZ) if date_to else None
    return start, end
