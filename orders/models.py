from django.db import models


class MarketplaceOrder(models.Model):
    """Pedido de un marketplace (Falabella, y a futuro otros) pendiente de
    importar a Shopify, o ya importado. Modelo general, no específico de un
    canal -- ver docs/implementations-plans/marketplace-orders-import.md.

    El indicador de "falta procesar" es `shopify_order_id == ""`, sin
    distinguir entre un pedido nuevo y uno que quedó en error en una
    corrida anterior -- así el reintento es automático: la siguiente
    corrida vuelve a intentarlo sin lógica aparte.
    """

    class Marketplace(models.TextChoices):
        FALABELLA = "falabella", "Falabella"
        # futuros marketplaces se agregan acá, no se crea un modelo por canal

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ERROR_CUSTOMER = "error_creando_cliente", "Error al crear cliente"
        ERROR_ORDER = "error_creando_orden", "Error al crear orden"
        CREATED = "orden_creada", "Orden creada"

    marketplace = models.CharField(max_length=20, choices=Marketplace.choices)
    marketplace_order_id = models.CharField(max_length=64)
    marketplace_order_number = models.CharField(max_length=64, blank=True)

    # Datos del comprador tal cual los reporta el marketplace -- necesarios
    # para crear el cliente en Shopify si no existe ya en el directorio
    # local (customers.ShopifyCustomer). No estaban en el diseño original
    # del plan; se agregaron al implementar porque sin esto no hay forma de
    # llamar a integrations.shopify.functions.create_customer.
    customer_identification = models.CharField(max_length=32, blank=True)
    customer_first_name = models.CharField(max_length=150, blank=True)
    customer_last_name = models.CharField(max_length=150, blank=True)
    customer_email = models.CharField(max_length=254, blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    error_description = models.TextField(blank=True)

    shopify_customer_id = models.CharField(max_length=32, blank=True)
    shopify_order_id = models.CharField(max_length=32, blank=True)
    shopify_order_name = models.CharField(max_length=32, blank=True)

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
    """Un ítem de un MarketplaceOrder. `shopify_variant_id` se cachea acá
    una vez resuelto por integrations.shopify.functions.get_variant_by_sku
    -- no hay todavía una tabla de equivalencias de SKU entre marketplaces
    (pospuesto a propósito, ver el plan)."""

    order = models.ForeignKey(MarketplaceOrder, related_name="items", on_delete=models.CASCADE)
    marketplace_sku = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.CharField(max_length=32, blank=True)
    shopify_variant_id = models.CharField(max_length=32, blank=True)

    class Meta:
        verbose_name = "Ítem de pedido de marketplace"
        verbose_name_plural = "Ítems de pedido de marketplace"

    def __str__(self):
        return f"{self.marketplace_sku} x{self.quantity}"
