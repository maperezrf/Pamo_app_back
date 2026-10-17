from integrations.shopify.functions.get_fulfillment_order_order_id import get_fulfillment_order_order_id
from integrations.shopify.functions.get_order import get_order

from ..models import ShopifyFulfillmentOrder, ShopifyOrder
from .assign_web_dispatch import assign_web_dispatch
from .dispatch_orders import follow_shopify_locations
from .mark_shopify_order_deleted import mark_shopify_order_deleted
from .upsert_shopify_order import upsert_shopify_order

DELETE_TOPIC = "orders/delete"
# Avisos de que alguien cambió la bodega en Shopify: el despacho la sigue.
# No incluye `order_routing_complete` ni `orders/*`: el enrutamiento inicial
# de Shopify no reemplaza la bodega que eligió este sistema por inventario.
LOCATION_CHANGE_TOPICS = {"fulfillment_orders/moved", "fulfillment_orders/split", "fulfillment_orders/merged"}


def process_shopify_order_webhook(params, progress_callback=None, cancellation_token=None):
    """Callable registrado en `orchestrator` como
    `orders.process_shopify_order_webhook` (`allow_concurrent=True`). Lo
    lanza `orders/webhooks.py` por cada aviso de pedido, fulfillment o
    fulfillment order ya verificado por firma.

    `params`: `{"topic", "order_id"}`, o `{"topic", "fulfillment_order_ids"}`
    en los avisos `fulfillment_orders/*` (el pedido se busca con
    `find_order_id`). El payload del aviso no se usa como dato: el pedido se
    vuelve a leer de Shopify (`get_order`), así un aviso repetido o
    desordenado deja siempre el estado actual. Si Shopify ya no lo
    encuentra, se trata como borrado.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    order_id = params.get("order_id") or find_order_id(params.get("fulfillment_order_ids") or [])
    if not order_id:
        progress_callback(100, f"Sin pedido para los fulfillment orders {params.get('fulfillment_order_ids')}")
        return
    if params.get("topic") == DELETE_TOPIC:
        mark_shopify_order_deleted(order_id)
        progress_callback(100, f"Pedido {order_id} marcado como borrado")
        return

    progress_callback(10, f"Leyendo pedido {order_id} de Shopify")
    data = get_order(order_id)
    if data is None:
        mark_shopify_order_deleted(order_id)
        progress_callback(100, f"Pedido {order_id} no existe en Shopify: marcado como borrado")
        return
    result = upsert_shopify_order(data)
    if params.get("topic") in LOCATION_CHANGE_TOPICS:
        order = ShopifyOrder.objects.prefetch_related("fulfillment_orders").get(shopify_id=data["id"])
        dispatches = follow_shopify_locations(order)
    else:
        # Pedido web pagado: se le asigna bodega al llegar (sin avisar a nadie).
        dispatches = assign_web_dispatch(data["id"]) or []
    names = [dispatch.location.name for dispatch in dispatches if dispatch.location]
    suffix = f"; bodega {', '.join(names)}" if names else ""
    suffix = suffix or ("; sin bodega (ver despacho)" if dispatches else "")
    progress_callback(100, f"Pedido {data['name'] or order_id}: {result}{suffix}")


def find_order_id(fulfillment_order_ids):
    """Pedido de unos fulfillment orders: primero en la copia local (sin
    llamar a Shopify) y, si ninguno está (fulfillment order nuevo, pedido
    aún sin copiar), en vivo con el primero que Shopify encuentre. `""` si
    no aparece."""
    local = (
        ShopifyFulfillmentOrder.objects.filter(shopify_id__in=fulfillment_order_ids)
        .values_list("order__shopify_id", flat=True)
        .first()
    )
    if local:
        return local
    for fulfillment_order_id in fulfillment_order_ids:
        order_id = get_fulfillment_order_order_id(fulfillment_order_id)
        if order_id:
            return order_id
    return ""
