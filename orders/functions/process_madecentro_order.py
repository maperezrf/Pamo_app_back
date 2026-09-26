from django.utils import timezone

from config.constants import MADECENTRO_SHOPIFY_CUSTOMER_ID
from integrations.madecentro.functions.get_order import get_order

from ..models import MarketplaceOrder, MarketplaceOrderItem
from .claim_orders import RETRYABLE_STATUSES, claim_orders
from .process_shipment import process_shipment

MARKETPLACE = MarketplaceOrder.Marketplace.MADECENTRO
PAID = "paid"

# Resultados posibles (texto para el progreso del orquestador y las pruebas).
ALREADY_HANDLED = "ya_procesado"
NOT_PAID = "no_pagado"
CLAIMED_ELSEWHERE = "en_proceso_por_otro"


def process_madecentro_order(order_id):
    """Lleva a Shopify el pedido `order_id` (id de Shipturtle) de
    Madecentro, a nombre del cliente fijo. Idempotente: lo usa la rutina
    `orders.import_madecentro` y lo reutilizará el webhook.

    1. Ya creado en Shopify o en `procesando` -> no se toca (no se llama
       a la API).
    2. Se lee el pedido. No pagado o cancelado -> se descarta la fila
       local si había una pendiente.
    3. Se guarda (o completa) la fila con el comprador y los ítems.
    4. Reclamo atómico (`procesando`) y `process_shipment`: un pedido = un
       envío = una orden de Shopify.

    Devuelve el estado final del pedido o una de las constantes de este
    módulo. Si falla la API, deja el detalle en `error_description` de la
    fila (si existe) y relanza. Ver
    docs/implementations-plans/madecentro-orders-import.md.
    """
    if not MADECENTRO_SHOPIFY_CUSTOMER_ID:
        raise ValueError("MADECENTRO_SHOPIFY_CUSTOMER_ID no está configurado")

    order_id = str(order_id)
    existing = MarketplaceOrder.objects.filter(marketplace=MARKETPLACE, marketplace_order_id=order_id).first()
    if existing and (existing.shopify_order_id or existing.status == MarketplaceOrder.Status.PROCESSING):
        return ALREADY_HANDLED

    try:
        data = get_order(order_id)
        print(data)
    except Exception as error:
        if existing:
            _open_rows(order_id).update(error_description=f"{type(error).__name__}: {error}"[:2000])
        raise

    if data["financial_status"] != PAID or data["cancelled"]:
        _open_rows(order_id).delete()
        return NOT_PAID

    _upsert(data)
    rows = claim_orders(MARKETPLACE, [order_id])
    if rows is None:
        return CLAIMED_ELSEWHERE
    # Los ítems se crean ya reclamado: dos procesos simultáneos del mismo
    # pedido no pueden duplicarlos. Los de un pedido pagado no cambian.
    if not rows[0].items.exists():
        MarketplaceOrderItem.objects.bulk_create(
            [
                MarketplaceOrderItem(
                    order=rows[0], marketplace_sku=item["sku"], quantity=item["quantity"], unit_price=item["price"]
                )
                for item in data["items"]
            ]
        )
    process_shipment(rows, MADECENTRO_SHOPIFY_CUSTOMER_ID)
    return MarketplaceOrder.objects.get(pk=rows[0].pk).status


def _upsert(data):
    """Guarda o actualiza los datos del pedido, solo mientras siga
    pendiente o en error: con un `update` condicionado al estado, nunca con
    `row.save()`, para no deshacer el reclamo de otro proceso (ver
    `_upsert` en process_mercadolibre_order.py)."""
    row, _ = MarketplaceOrder.objects.get_or_create(marketplace=MARKETPLACE, marketplace_order_id=data["order_id"])
    MarketplaceOrder.objects.filter(pk=row.pk, shopify_order_id="", status__in=RETRYABLE_STATUSES).update(
        marketplace_order_number=data["order_number"],
        customer_first_name=data["customer_first_name"],
        customer_last_name=data["customer_last_name"],
        customer_email=data["customer_email"],
        customer_phone=data["customer_phone"],
        customer_address=data["customer_address"],
        customer_city=data["customer_city"],
        customer_region=data["customer_region"],
        updated_at=timezone.now(),
    )


def _open_rows(order_id):
    # Fila local que todavía no llegó a Shopify ni está en proceso.
    return MarketplaceOrder.objects.filter(
        marketplace=MARKETPLACE, marketplace_order_id=order_id, shopify_order_id=""
    ).exclude(status=MarketplaceOrder.Status.PROCESSING)
