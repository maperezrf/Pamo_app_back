from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from integrations.shopify.functions.get_order import get_order

from ..models import ShopifyOrder, ShopifyOrderLine
from .mark_shopify_order_deleted import mark_shopify_order_deleted
from .order_listing_format import CENTS, to_money
from .shopify_order_marketplace import marketplace_from_tags

CREATED = "created"
UPDATED = "updated"
SKIPPED = "skipped"
DELETED = "deleted"


def upsert_shopify_order(data):
    """Crea o actualiza la copia local de un pedido de Shopify ya normalizado
    (forma de `integrations.shopify.functions.normalize_order`). **Punto único
    de "cómo se guarda un pedido"**: lo usan el webhook, la carga inicial y
    la reconciliación.

    - Un pedido con `line_items_truncated` se vuelve a leer completo con
      `get_order` (si ya no existe en Shopify, se marca borrado).
    - Un pedido borrado localmente no se toca: un aviso tardío no lo revive.
    - Un dato con `updated_at` anterior al guardado no pisa al más nuevo
      (avisos desordenados).
    - Si no, actualiza la cabecera y reemplaza todas las líneas, en una
      transacción con la fila bloqueada. Dos avisos simultáneos del mismo
      pedido nuevo chocan en el `unique` de `shopify_id`: se reintenta una
      vez y el segundo ya encuentra la fila.

    Devuelve `created`, `updated`, `skipped` o `deleted`.
    """
    if data.get("line_items_truncated"):
        order_id = data["id"]
        data = get_order(order_id)
        if data is None:
            mark_shopify_order_deleted(order_id)
            return DELETED
    try:
        return _upsert(data)
    except IntegrityError:
        return _upsert(data)


def _upsert(data):
    updated_at = parse_datetime(data["updated_at"]) if data.get("updated_at") else None
    with transaction.atomic():
        order = ShopifyOrder.objects.select_for_update().filter(shopify_id=data["id"]).first()
        if order is not None:
            if order.deleted_at is not None:
                return SKIPPED
            if order.shopify_updated_at and updated_at and updated_at < order.shopify_updated_at:
                return SKIPPED

        fields = _order_fields(data, updated_at)
        if order is None:
            order = ShopifyOrder.objects.create(shopify_id=data["id"], **fields)
            result = CREATED
        else:
            for name, value in fields.items():
                setattr(order, name, value)
            order.save()
            order.lines.all().delete()
            result = UPDATED

        ShopifyOrderLine.objects.bulk_create(
            [_line(order, position, item) for position, item in enumerate(data["line_items"])]
        )
        return result


def _order_fields(data, updated_at):
    customer = data.get("customer") or {}
    return {
        "name": data["name"],
        "shopify_created_at": parse_datetime(data["created_at"]) if data.get("created_at") else None,
        "shopify_updated_at": updated_at,
        "cancelled_at": parse_datetime(data["cancelled_at"]) if data.get("cancelled_at") else None,
        "financial_status": data["financial_status"],
        "fulfillment_status": data["fulfillment_status"],
        "tags": data["tags"],
        "marketplace": marketplace_from_tags(data["tags"]),
        "email": data["email"][:254],
        "phone": data["phone"][:32],
        "total": to_money(data["total"]),
        "currency": data["currency"],
        "customer_id": customer.get("id", ""),
        "customer_first_name": customer.get("first_name", "")[:150],
        "customer_last_name": customer.get("last_name", "")[:150],
        "customer_identification": customer.get("identification", "")[:32],
        "customer_city": customer.get("city", "")[:100],
        "customer_region": customer.get("region", "")[:100],
        "customer_address": customer.get("address", "")[:255],
    }


def _line(order, position, item):
    price = to_money(item["unit_price"])
    line_total = None if price is None else (price * item["quantity"]).quantize(CENTS)
    return ShopifyOrderLine(
        order=order,
        position=position,
        sku=item["sku"][:64],
        name=item["name"][:255],
        quantity=item["quantity"],
        unit_price=price,
        line_total=line_total,
    )
