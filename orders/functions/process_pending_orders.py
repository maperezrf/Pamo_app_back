from customers.functions.find_by_identification import find_by_identification
from customers.functions.upsert_from_shopify_customer import upsert_from_shopify_customer
from integrations.shopify.functions.create_customer import ShopifyCustomerCreationError, create_customer
from integrations.shopify.functions.create_customer_address import create_customer_address
from integrations.shopify.functions.create_order import ShopifyOrderCreationError, create_order
from integrations.shopify.functions.get_variant_by_sku import get_variant_by_sku

from ..models import MarketplaceOrder

# Los pedidos de marketplace llegan ya pagados por el comprador -- no se
# está cobrando nada a través de Shopify, solo se registra la venta.
FINANCIAL_STATUS = "PAID"


def process_pending_orders(progress_callback=None, cancellation_token=None, limit=None):
    """Por cada MarketplaceOrder sin `shopify_order_id` (nuevo o de un
    error anterior -- mismo criterio, sin distinción, ver
    docs/implementations-plans/marketplace-orders-import.md), intenta
    resolver el cliente, los SKUs, y crear la orden en Shopify.

    Un pedido que falla queda marcado con su estado de error y no detiene
    el resto del lote.

    `limit`: si se da, procesa como máximo esa cantidad de pedidos
    pendientes (los más antiguos primero) -- pensado para una primera
    corrida controlada contra la cuenta real (ej. `limit=1`), no para uso
    normal. `None` procesa todos los pendientes.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    pending_queryset = MarketplaceOrder.objects.filter(shopify_order_id="").order_by("created_at").prefetch_related(
        "items"
    )
    if limit:
        pending_queryset = pending_queryset[:limit]
    pending = list(pending_queryset)
    progress_callback(5, f"{len(pending)} pedidos pendientes")

    total = len(pending) or 1
    for index, order in enumerate(pending):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        _process_one_order(order)
        progress_callback(
            5 + int(90 * (index + 1) / total),
            f"Pedido {order.marketplace_order_number or order.marketplace_order_id}",
        )

    progress_callback(100, f"{len(pending)} pedidos pendientes procesados")


def _process_one_order(order):
    shopify_customer = find_by_identification(order.customer_identification)
    if shopify_customer is None:
        shopify_customer = _create_shopify_customer(order)
        if shopify_customer is None:
            return  # ya quedó marcado error_creando_cliente

    line_items = _resolve_line_items(order)
    if line_items is None:
        return  # ya quedó marcado error_creando_orden

    try:
        result = create_order(
            items=line_items,
            customer_id=shopify_customer.shopify_id,
            financial_status=FINANCIAL_STATUS,
            note=f"{order.get_marketplace_display()} #{order.marketplace_order_number}",
            tags=[order.marketplace],
        )
    except ShopifyOrderCreationError as error:
        order.status = MarketplaceOrder.Status.ERROR_ORDER
        order.error_description = str(error)
        order.save(update_fields=["status", "error_description", "updated_at"])
        return

    order.status = MarketplaceOrder.Status.CREATED
    order.shopify_customer_id = shopify_customer.shopify_id
    order.shopify_order_id = result["order_id"]
    order.shopify_order_name = result["order_name"]
    order.error_description = ""
    order.save(
        update_fields=[
            "status",
            "shopify_customer_id",
            "shopify_order_id",
            "shopify_order_name",
            "error_description",
            "updated_at",
        ]
    )


def _create_shopify_customer(order):
    try:
        shopify_id = create_customer(
            email=order.customer_email,
            first_name=order.customer_first_name,
            last_name=order.customer_last_name,
        )
    except (ValueError, ShopifyCustomerCreationError) as error:
        order.status = MarketplaceOrder.Status.ERROR_CUSTOMER
        order.error_description = str(error)
        order.save(update_fields=["status", "error_description", "updated_at"])
        return None

    address_id = create_customer_address(
        customer_id=shopify_id,
        company=order.customer_identification,
        first_name=order.customer_first_name,
        last_name=order.customer_last_name,
    )
    return upsert_from_shopify_customer(
        {
            "shopify_id": shopify_id,
            "identification": order.customer_identification,
            "first_name": order.customer_first_name,
            "last_name": order.customer_last_name,
            "email": order.customer_email,
            "phone": "",
            "default_address_id": address_id,
            "shopify_updated_at": None,
        }
    )


def _resolve_line_items(order):
    line_items = []
    for item in order.items.all():
        variant_id = item.shopify_variant_id or get_variant_by_sku(item.marketplace_sku)
        if not variant_id:
            order.status = MarketplaceOrder.Status.ERROR_ORDER
            order.error_description = f"SKU no encontrado en Shopify: {item.marketplace_sku}"
            order.save(update_fields=["status", "error_description", "updated_at"])
            return None
        if not item.shopify_variant_id:
            item.shopify_variant_id = variant_id
            item.save(update_fields=["shopify_variant_id"])
        line_items.append({"variant_id": variant_id, "quantity": item.quantity, "price": item.unit_price})
    return line_items
