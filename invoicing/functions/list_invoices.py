"""Listado de facturas para `GET /api/invoicing/`. Solo lee la base de
datos: nunca consulta Siigo. Ver docs/apps/invoicing.md ("Listado")."""

from django.db.models import Q
from django.utils import timezone

from orders.functions.order_listing_format import format_line, money_str

from ..models import Invoice


def filter_invoices(*, status=None, marketplace=None, date_from=None, date_to=None, search=""):
    """Queryset de facturas, más reciente primero.

    - Fechas: inclusive, sobre la fecha de la factura (`invoice_date`).
    - `search`: OC (número del pedido en el marketplace), nombre del pedido
      en Shopify (sin `#`) o número de la factura en Siigo (`FV-1-…`).
    """
    invoices = Invoice.objects.select_related("marketplace_order")
    if status:
        invoices = invoices.filter(status=status)
    if marketplace:
        invoices = invoices.filter(marketplace_order__marketplace=marketplace)
    if date_from:
        invoices = invoices.filter(invoice_date__gte=date_from)
    if date_to:
        invoices = invoices.filter(invoice_date__lte=date_to)
    search = (search or "").strip().lstrip("#")
    if search:
        invoices = invoices.filter(
            Q(purchase_order_number=search)
            | Q(marketplace_order__marketplace_order_number=search)
            | Q(marketplace_order__shopify_order_name__in=[search, f"#{search}"])
            | Q(number__iexact=search)
        )
    return invoices.order_by("-invoice_date", "-id")


def format_invoices(invoices):
    return [_format_invoice(invoice) for invoice in invoices]


def _format_invoice(invoice):
    order = invoice.marketplace_order
    return {
        "id": invoice.id,
        "status": invoice.status,
        "error_message": invoice.error_message,
        "invoice_date": invoice.invoice_date.isoformat(),
        "created_at": _iso(invoice.created_at),
        "updated_at": _iso(invoice.updated_at),
        "number": invoice.number,
        "siigo_id": invoice.siigo_id,
        # Link para ver la factura que devuelve Siigo al crearla
        # (`public_url`). Vacío si no se creó.
        "siigo_url": (invoice.response_payload or {}).get("public_url", "") if invoice.siigo_id else "",
        "stamped": invoice.stamped,
        "customer_name": invoice.customer_name,
        "customer_identification": invoice.customer_identification,
        "marketplace": order.marketplace,
        "marketplace_order_number": order.marketplace_order_number or invoice.purchase_order_number,
        "shopify_order_id": order.shopify_order_id,
        "shopify_order_name": order.shopify_order_name,
        "subtotal": money_str(invoice.subtotal),
        "iva": money_str(invoice.iva),
        "reteiva": money_str(invoice.reteiva),
        "reteica": money_str(invoice.reteica),
        "retefuente": money_str(invoice.retefuente),
        "total": money_str(invoice.total),
        "siigo_total": money_str(invoice.siigo_total),
        "items": [_format_item(item) for item in _payload_items(invoice)],
    }


def _payload_items(invoice):
    # Las líneas quedan en `request_payload["items"]` (lo enviado a Siigo).
    items = (invoice.request_payload or {}).get("items")
    return items if isinstance(items, list) else []


def _format_item(item):
    # `code` es el SKU de Pamo, o el del marketplace si no tiene
    # equivalencia (ver "Cálculo" en docs/apps/invoicing.md).
    line, _ = format_line({"sku": str(item.get("code", ""))}, item.get("price"), item.get("quantity", 0))
    return line


def _iso(value):
    return timezone.localtime(value).isoformat() if value else None
