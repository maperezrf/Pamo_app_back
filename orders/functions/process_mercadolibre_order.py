from collections import Counter

from django.db import transaction
from django.utils import timezone

from config.constants import MERCADOLIBRE_SHOPIFY_CUSTOMER_ID
from integrations.mercadolibre.functions.get_billing_info import get_billing_info
from integrations.mercadolibre.functions.get_order import get_order
from integrations.mercadolibre.functions.get_pack import get_pack
from integrations.mercadolibre.functions.get_shipment import LOGISTIC_TYPE_FULFILLMENT, get_shipment

from ..models import MarketplaceOrder, MarketplaceOrderItem
from .process_shipment import process_shipment

MARKETPLACE = MarketplaceOrder.Marketplace.MERCADOLIBRE
PAID = "paid"
RETRYABLE_STATUSES = (MarketplaceOrder.Status.PENDING, MarketplaceOrder.Status.ERROR_ORDER)
INCOMPLETE_PREFIX = "Envío incompleto"

# Resultados posibles (texto para el progreso del orquestador y las pruebas).
ALREADY_HANDLED = "ya_procesado"
NOT_PAID = "no_pagado"
FULFILLMENT = "full_omitido"
INCOMPLETE = "envio_incompleto"
CLAIMED_ELSEWHERE = "en_proceso_por_otro"


def process_mercadolibre_order(order_id):
    """Lleva a Shopify el pedido `order_id` de Mercado Libre junto con las
    demás órdenes de su envío. Lo usan el webhook y la recuperación, y es
    idempotente: se puede llamar varias veces por el mismo pedido.

    Flujo (ver docs/implementations-plans/mercadolibre-orders-import.md):
    1. Marcador local (`MarketplaceOrder` vacío) **antes** de llamar a
       Mercado Libre: si algo falla, la recuperación lo encuentra.
    2. No pagado -> se descarta el marcador (llegará otra notificación).
       Full -> igual (despacha Mercado Libre).
    3. Órdenes del envío: las del pack (`get_pack`) o solo esta. Se
       persisten las pagadas con sus datos de facturación.
    4. Envío incompleto (una orden del pack sin pagar, o los ítems no
       suman lo que lleva el envío) -> no se procesa todavía.
    5. Reclamo atómico de todas las órdenes del envío (`procesando`) y
       `process_shipment`: una bodega y una orden de Shopify.

    Devuelve el resultado: el estado final del pedido o una de las
    constantes de este módulo. Si falla una llamada a Mercado Libre, deja
    el detalle en `error_description` del marcador y relanza (la
    ejecución del orquestador queda en error y la recuperación reintenta).
    """
    if not MERCADOLIBRE_SHOPIFY_CUSTOMER_ID:
        raise ValueError("MERCADOLIBRE_SHOPIFY_CUSTOMER_ID no está configurado")

    order_id = str(order_id)
    marker, _ = MarketplaceOrder.objects.get_or_create(marketplace=MARKETPLACE, marketplace_order_id=order_id)
    if marker.shopify_order_id or marker.status == MarketplaceOrder.Status.PROCESSING:
        return ALREADY_HANDLED
    try:
        return _process(order_id, marker)
    except Exception as error:
        MarketplaceOrder.objects.filter(pk=marker.pk, shopify_order_id="").exclude(
            status=MarketplaceOrder.Status.PROCESSING
        ).update(error_description=f"{type(error).__name__}: {error}"[:2000])
        raise


def _process(order_id, marker):
    order = get_order(order_id)
    if order["status"] != PAID:
        _discard(marker)
        return NOT_PAID

    shipment = get_shipment(order["shipment_id"]) if order["shipment_id"] else None
    if shipment and shipment["logistic_type"] == LOGISTIC_TYPE_FULFILLMENT:
        _discard(marker)
        return FULFILLMENT

    order_ids = get_pack(order["pack_id"])["order_ids"] if order["pack_id"] else [order_id]
    if order_id not in order_ids:
        order_ids.append(order_id)
    fetched = {order_id: order}
    for other_id in order_ids:
        if other_id not in fetched:
            fetched[other_id] = get_order(other_id)

    number = order["pack_id"] or order_id
    for data in fetched.values():
        if data["status"] == PAID:
            _upsert(data, number, order["shipment_id"])

    unpaid = [other_id for other_id, data in fetched.items() if data["status"] != PAID]
    if unpaid:
        _mark_incomplete(order_ids, f"{INCOMPLETE_PREFIX}: órdenes del pack sin pagar {', '.join(unpaid)}")
        return INCOMPLETE
    if shipment and not _items_match(fetched.values(), shipment["items"]):
        _mark_incomplete(order_ids, f"{INCOMPLETE_PREFIX}: los ítems de las órdenes no suman lo que lleva el envío")
        return INCOMPLETE

    rows = _claim(order_ids)
    if rows is None:
        return CLAIMED_ELSEWHERE
    process_shipment(rows, MERCADOLIBRE_SHOPIFY_CUSTOMER_ID)
    return MarketplaceOrder.objects.get(pk=marker.pk).status


def _upsert(data, number, shipment_id):
    """Guarda (o completa) una orden pagada. Una orden ya creada en
    Shopify o en proceso no se toca. Los datos de facturación se piden una
    sola vez; los ítems de una orden pagada no cambian."""
    row, _ = MarketplaceOrder.objects.get_or_create(marketplace=MARKETPLACE, marketplace_order_id=data["order_id"])
    if row.shopify_order_id or row.status == MarketplaceOrder.Status.PROCESSING:
        return row

    row.marketplace_order_number = number
    row.shipment_id = shipment_id
    if not row.customer_identification and data["billing_info_id"]:
        billing = get_billing_info(data["billing_info_id"])
        row.customer_identification_type = billing["customer_identification_type"]
        row.customer_identification = billing["customer_identification"]
        row.customer_type = billing["customer_type"]
        # Nombre de facturación (razón social en NIT); si no viene, el del pedido.
        row.customer_first_name = billing["customer_first_name"] or data["buyer"]["first_name"]
        row.customer_last_name = billing["customer_last_name"] or (
            "" if billing["customer_first_name"] else data["buyer"]["last_name"]
        )
        row.customer_address = billing["customer_address"]
        row.customer_city = billing["customer_city"]
        row.customer_region = billing["customer_region"]
    row.save()

    if not row.items.exists():
        MarketplaceOrderItem.objects.bulk_create(
            [
                MarketplaceOrderItem(
                    order=row, marketplace_sku=item["sku"], quantity=item["quantity"], unit_price=item["price"]
                )
                for item in data["items"]
            ]
        )
    return row


def _items_match(orders, shipment_items):
    # `shipping_items` viene por id de publicación (MCO...), no por SKU.
    if not shipment_items:
        return True
    ordered = Counter()
    for data in orders:
        for item in data["items"]:
            ordered[item["item_id"]] += item["quantity"]
    shipped = Counter()
    for item in shipment_items:
        shipped[item["item_id"]] += item["quantity"]
    return ordered == shipped


def _claim(order_ids):
    """Pasa a `procesando` TODAS las órdenes del envío o ninguna. Si alguna
    ya no está pendiente/en error (otro proceso la tomó o ya se creó),
    devuelve None."""
    with transaction.atomic():
        rows = list(
            MarketplaceOrder.objects.select_for_update()
            .filter(marketplace=MARKETPLACE, marketplace_order_id__in=order_ids)
            .order_by("pk")
        )
        if len(rows) != len(set(order_ids)):
            return None
        if any(row.shopify_order_id or row.status not in RETRYABLE_STATUSES for row in rows):
            return None
        MarketplaceOrder.objects.filter(pk__in=[row.pk for row in rows]).update(
            status=MarketplaceOrder.Status.PROCESSING, updated_at=timezone.now()
        )
        for row in rows:
            row.status = MarketplaceOrder.Status.PROCESSING
    return rows


def _mark_incomplete(order_ids, description):
    MarketplaceOrder.objects.filter(
        marketplace=MARKETPLACE, marketplace_order_id__in=order_ids, shopify_order_id="", status__in=RETRYABLE_STATUSES
    ).update(error_description=description)


def _discard(marker):
    # Solo si nunca llegó a Shopify ni está en proceso: un pedido cancelado
    # o no pagado no debe quedar para la recuperación.
    MarketplaceOrder.objects.filter(pk=marker.pk, shopify_order_id="").exclude(
        status=MarketplaceOrder.Status.PROCESSING
    ).delete()
