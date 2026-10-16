from decimal import Decimal

from products.models import Marketplace

from ..models import MarketplaceOrder
from .order_listing_format import buyer_from_row, money_str
from .shopify_order_marketplace import SHOPIFY_CHANNEL


def dispatch_lines(dispatch):
    """Productos que lleva el despacho: `[{sku, name, quantity,
    unit_price}]` (`unit_price` como `Decimal`).

    - Despacho de todo el pedido: las líneas del pedido.
    - Despacho de una bodega de un pedido repartido (`split`): las del
      fulfillment order de Shopify de esa bodega; el precio, el de la línea
      del pedido con el mismo SKU (el fulfillment order no lo trae).
    """
    order = dispatch.shopify_order
    lines = list(order.lines.all())
    if not dispatch.split:
        return [
            {"sku": line.sku, "name": line.name, "quantity": line.quantity, "unit_price": line.unit_price}
            for line in lines
        ]
    location_id = dispatch.location.shopify_location_id if dispatch.location else ""
    prices = {line.sku: line.unit_price for line in lines}
    names = {line.sku: line.name for line in lines}
    return [
        {
            "sku": line.sku,
            "name": line.name or names.get(line.sku, ""),
            "quantity": line.quantity,
            "unit_price": prices.get(line.sku),
        }
        for item in order.fulfillment_orders.all()
        if item.location_id == location_id
        for line in item.lines.all()
    ]


def lines_total(lines):
    return sum((line["unit_price"] or Decimal("0")) * line["quantity"] for line in lines)


def dispatch_details(dispatch):
    """Lo que la bodega necesita para despachar el `Dispatch` (su pedido de
    Shopify, solo con los productos de ese despacho), igual para todos los
    canales de aviso.

    Comprador: el real de `MarketplaceOrder` en pedidos de marketplace (en
    Shopify van al cliente fijo del canal); el de Shopify en la tienda web.
    La dirección es la que se guarda localmente (en la tienda web, la del
    cliente en Shopify); la guía del canal trae la de entrega.
    """
    order = dispatch.shopify_order
    lines = dispatch_lines(dispatch)
    rows = list(MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id"))
    buyer = buyer_from_row(rows[0]) if rows else _shopify_buyer(order)
    channel = order.marketplace or SHOPIFY_CHANNEL
    return {
        "order_name": order.name,
        "channel": channel,
        "channel_label": dict(Marketplace.choices).get(channel, "Tienda web"),
        "marketplace_numbers": sorted({row.marketplace_order_number or row.marketplace_order_id for row in rows}),
        "buyer": buyer,
        "items": [{**line, "unit_price": money_str(line["unit_price"])} for line in lines],
        # En un despacho de una bodega, lo de sus productos.
        "total": money_str(lines_total(lines) if dispatch.split else order.total),
        "currency": order.currency or "COP",
    }


def dispatch_text(details, location, tracking_number=""):
    """Texto del aviso para correo y vista previa: pedido, productos y
    destinatario. Sin datos que la bodega no necesite para despachar."""
    buyer = details["buyer"]
    name = " ".join(part for part in (buyer["first_name"], buyer["last_name"]) if part)
    lines = [
        f"Pedido {details['order_name']} ({details['channel_label']}"
        + (f" #{', #'.join(details['marketplace_numbers'])}" if details["marketplace_numbers"] else "")
        + f") - despacha {location.name}",
        "",
        "Productos:",
        *[f"- {item['sku'] or '(sin SKU)'} x{item['quantity']}  {item['name']}" for item in details["items"]],
        "",
        "Destinatario:",
        f"{name or '-'}",
        f"{buyer['address'] or '-'}, {buyer['city'] or '-'}, {buyer['region'] or '-'}",
        f"Teléfono: {buyer['phone'] or '-'}",
    ]
    if tracking_number:
        lines += ["", f"Guía: {tracking_number} (adjunta)"]
    return "\n".join(lines)


def items_summary(details):
    return ", ".join(f"{item['sku'] or item['name']} x{item['quantity']}" for item in details["items"])


def _shopify_buyer(order):
    return {
        "identification_type": "",
        "identification": order.customer_identification,
        "first_name": order.customer_first_name,
        "last_name": order.customer_last_name,
        "email": order.email,
        "phone": order.phone,
        "city": order.customer_city,
        "region": order.customer_region,
        "address": order.customer_address,
    }
