"""Listado de cotizaciones para `GET /api/quotes/`. Lee Shopify en vivo (no
hay copia local). Ver docs/apps/quotes.md."""

from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from integrations.shopify.functions.list_draft_orders import list_draft_orders

from .quote_links import quote_link

# Las más recientes; para una más vieja se busca (`search`). Costo medido:
# 24 puntos con 250 (2026-10-22).
LIST_SIZE = 250


def list_quotes(search=""):
    """Filas del listado, más reciente primero: `id` (draft order sin
    `gid://`), `name`, `created_at` (`YYYY-MM-DD`, Colombia), `customer`,
    `total` (string con 2 decimales), `seller` (primera etiqueta, como en
    pamo_web), `nit` (lo que va antes del `-` en la empresa de la dirección
    principal) y `pdf_link` (público firmado, para el cliente)."""
    drafts = list_draft_orders(first=LIST_SIZE, query=(search or "").strip())
    return [_row(draft) for draft in drafts]


def _row(draft):
    customer = draft["customer"] or {}
    name = f"{customer.get('first_name', '').title()} {customer.get('last_name', '').title()}".strip()
    created_at = parse_datetime(draft["created_at"]) if draft["created_at"] else None
    return {
        "id": draft["id"],
        "name": draft["name"],
        "created_at": timezone.localtime(created_at).date().isoformat() if created_at else "",
        "customer": name,
        "total": _money(draft["total"]),
        "seller": draft["tags"][0] if draft["tags"] else "",
        "nit": customer.get("company", "").split("-")[0].strip()[:24],
        "pdf_link": quote_link(draft["id"]),
    }


def _money(amount):
    try:
        return str(Decimal(amount).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError):
        return ""
