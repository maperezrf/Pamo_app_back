from products.models import Marketplace

from ..models import MarketplaceOrder
from .order_listing_format import buyer_from_row, money_str
from .shopify_order_marketplace import SHOPIFY_CHANNEL


def dispatch_details(order):
    """Lo que la bodega necesita para despachar el pedido de Shopify
    `order`, igual para todos los canales de aviso.

    Comprador: el real de `MarketplaceOrder` en pedidos de marketplace (en
    Shopify van al cliente fijo del canal); el de Shopify en la tienda web.
    La dirección es la que se guarda localmente (en la tienda web, la del
    cliente en Shopify); la guía del canal trae la de entrega.
    """
    rows = list(MarketplaceOrder.objects.filter(shopify_order_id=order.shopify_id).order_by("id"))
    buyer = buyer_from_row(rows[0]) if rows else _shopify_buyer(order)
    channel = order.marketplace or SHOPIFY_CHANNEL
    return {
        "order_name": order.name,
        "channel": channel,
        "channel_label": dict(Marketplace.choices).get(channel, "Tienda web"),
        "marketplace_numbers": sorted({row.marketplace_order_number or row.marketplace_order_id for row in rows}),
        "buyer": buyer,
        "items": [
            {"sku": line.sku, "name": line.name, "quantity": line.quantity, "unit_price": money_str(line.unit_price)}
            for line in order.lines.all()
        ],
        "total": money_str(order.total),
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
