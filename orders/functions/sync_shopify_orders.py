from collections import Counter

from integrations.shopify.functions.list_orders_page import list_orders_page

from .order_listing_format import COLOMBIA_TZ
from .upsert_shopify_order import upsert_shopify_order

PAGE_SIZE = 50


def iter_shopify_order_pages(query):
    """Recorre todas las páginas de pedidos de Shopify que cumplen `query`,
    ordenadas por fecha de actualización ascendente, y guarda cada uno con
    `upsert_shopify_order`. Bucle compartido por la carga inicial y la
    reconciliación.

    Es un generador: entrega `(número de página, Counter acumulado de
    resultados)` después de cada página, para que el proceso registrado
    reporte progreso y revise la cancelación entre páginas. Un error de
    Shopify se propaga.
    """
    cursor = None
    page_number = 0
    counts = Counter()
    while True:
        page = list_orders_page(first=PAGE_SIZE, after=cursor, query=query, sort_key="UPDATED_AT", reverse=False)
        for order in page["orders"]:
            counts[upsert_shopify_order(order)] += 1
        page_number += 1
        yield page_number, counts
        if not page["has_next_page"]:
            return
        cursor = page["end_cursor"]


def shopify_datetime(value):
    """Fecha y hora para la búsqueda de Shopify, en hora de Colombia y entre
    comillas (`'2026-10-03T00:00:00-05:00'`)."""
    return f"'{value.astimezone(COLOMBIA_TZ).isoformat(timespec='seconds')}'"


def summary(counts):
    return ", ".join(f"{name}: {counts[name]}" for name in ("created", "updated", "skipped", "deleted") if counts[name]) or "sin pedidos"
