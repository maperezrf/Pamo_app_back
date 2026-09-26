from django.db import models

from products.models import Marketplace


class MarketplaceOrder(models.Model):
    """Pedido de un marketplace (Falabella, y a futuro otros) pendiente de
    importar a Shopify, o ya importado. Modelo general, no específico de un
    canal -- ver docs/implementations-plans/marketplace-orders-import.md.

    El indicador de "falta procesar" es `shopify_order_id == ""`, sin
    distinguir entre un pedido nuevo y uno que quedó en error en una
    corrida anterior -- así el reintento es automático: la siguiente
    corrida vuelve a intentarlo sin lógica aparte.
    """

    # Alias: la lista de canales vive en `products.models.Marketplace` (el
    # catálogo también la usa y `products` no depende de `orders`). Un
    # marketplace nuevo se agrega allá, no se crea un modelo por canal.
    Marketplace = Marketplace

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        # Reclamado por un proceso que está creando la orden en Shopify (solo
        # Mercado Libre, donde varios webhooks del mismo pedido pueden llegar
        # a la vez). Si el proceso muere acá, no se sabe si la orden quedó
        # creada: NO se reintenta solo, se revisa a mano -- ver
        # docs/implementations-plans/mercadolibre-orders-import.md (decisión F).
        PROCESSING = "procesando", "Procesando"
        # Obsoleto: ya no se crean clientes en Shopify (todos los pedidos van
        # al cliente fijo). Se conserva por las filas históricas; la próxima
        # corrida las reintenta porque no tienen shopify_order_id.
        ERROR_CUSTOMER = "error_creando_cliente", "Error al crear cliente"
        ERROR_ORDER = "error_creando_orden", "Error al crear orden"
        CREATED = "orden_creada", "Orden creada"

    class FulfillmentStatus(models.TextChoices):
        PENDING = "", "Sin evaluar"
        ASSIGNED = "asignada", "Bodega asignada"
        NOVEDAD = "novedad", "Novedad"
        RESOLVED = "resuelta_manual", "Resuelta manualmente"

    marketplace = models.CharField(max_length=20, choices=Marketplace.choices)
    marketplace_order_id = models.CharField(max_length=64)
    marketplace_order_number = models.CharField(max_length=64, blank=True)

    # Envío del marketplace (solo Mercado Libre). Un envío = una guía = una
    # bodega = una orden de Shopify: las órdenes con el mismo shipment_id
    # (packs) se procesan juntas. Vacío en Falabella (un pedido = un envío).
    shipment_id = models.CharField(max_length=32, blank=True, db_index=True)

    # Datos del comprador tal cual los reporta el marketplace. En Shopify la
    # orden queda a nombre de un cliente fijo por canal
    # (FALABELLA_, MERCADOLIBRE_, MADECENTRO_SHOPIFY_CUSTOMER_ID); estos
    # campos son la fuente para facturar en Siigo -- ver
    # docs/implementations-plans/falabella-fixed-customer.md.
    customer_identification_type = models.CharField(max_length=16, blank=True)  # CC, NIT... (Mercado Libre)
    customer_type = models.CharField(max_length=8, blank=True)  # CO persona / BU empresa (Mercado Libre)
    customer_identification = models.CharField(max_length=32, blank=True)
    customer_first_name = models.CharField(max_length=150, blank=True)
    customer_last_name = models.CharField(max_length=150, blank=True)
    customer_email = models.CharField(max_length=254, blank=True)
    customer_address = models.CharField(max_length=255, blank=True)
    customer_city = models.CharField(max_length=100, blank=True)
    customer_region = models.CharField(max_length=100, blank=True)
    customer_phone = models.CharField(max_length=32, blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    error_description = models.TextField(blank=True)

    shopify_customer_id = models.CharField(max_length=32, blank=True)
    shopify_order_id = models.CharField(max_length=32, blank=True)
    shopify_order_name = models.CharField(max_length=32, blank=True)

    # Bodega de despacho: una por pedido (el marketplace da una guía por
    # pedido). Independiente de `status`: la orden en Shopify se crea
    # igual aunque haya novedad. Ver
    # docs/implementations-plans/shopify-inventory-by-location.md.
    fulfillment_status = models.CharField(
        max_length=20, choices=FulfillmentStatus.choices, blank=True, default=FulfillmentStatus.PENDING
    )
    fulfillment_location_id = models.CharField(max_length=32, blank=True)
    fulfillment_location_name = models.CharField(max_length=100, blank=True)
    fulfillment_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pedido de marketplace"
        verbose_name_plural = "Pedidos de marketplace"
        constraints = [
            models.UniqueConstraint(
                fields=["marketplace", "marketplace_order_id"], name="unique_marketplace_order"
            )
        ]

    def __str__(self):
        return f"{self.marketplace}:{self.marketplace_order_number or self.marketplace_order_id}"


class MarketplaceOrderItem(models.Model):
    """Un ítem de un MarketplaceOrder. `marketplace_sku` es el SKU tal cual
    lo reporta el marketplace; `process_shipment` lo traduce con el
    catálogo de `products` (equivalencias) antes de buscarlo en Shopify.
    `shopify_variant_id` se cachea acá una vez resuelto por
    integrations.shopify.functions.get_variant_inventory_by_sku.
    `inventory_snapshot` guarda las unidades por bodega que se evaluaron al
    elegir la bodega del pedido."""

    order = models.ForeignKey(MarketplaceOrder, related_name="items", on_delete=models.CASCADE)
    marketplace_sku = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.CharField(max_length=32, blank=True)
    shopify_variant_id = models.CharField(max_length=32, blank=True)
    inventory_snapshot = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Ítem de pedido de marketplace"
        verbose_name_plural = "Ítems de pedido de marketplace"

    def __str__(self):
        return f"{self.marketplace_sku} x{self.quantity}"
