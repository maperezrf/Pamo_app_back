from django.core.exceptions import ValidationError
from django.db import models


class Marketplace(models.TextChoices):
    """Canales de venta de Pamo. Vive acá (no en `orders`) porque el
    catálogo lo necesita y `products` no depende de `orders`;
    `orders.MarketplaceOrder.Marketplace` es un alias de esta clase.
    Un marketplace nuevo se agrega acá, no se crea un modelo por canal."""

    FALABELLA = "falabella", "Falabella"
    MERCADOLIBRE = "mercadolibre", "Mercado Libre"
    MADECENTRO = "madecentro", "Madecentro"
    SODIMAC = "sodimac", "Sodimac"


class Product(models.Model):
    """Producto del catálogo de Pamo -- ver
    docs/implementations-plans/products-catalog.md.

    `sku` es el SKU de Pamo: en un producto simple, el que existe en
    Shopify. Un kit (`is_kit=True`) NO existe en Shopify: a Shopify van sus
    componentes (`KitComponent`). Los kits traídos de pamo_web usan como SKU
    el que les da Sodimac, porque allá no tenían otro nombre.
    """

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    is_kit = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ["sku"]

    def __str__(self):
        return f"{self.sku} (kit)" if self.is_kit else self.sku


class KitComponent(models.Model):
    """Relación N:M kit ↔ producto con cantidad: un kit tiene varios
    productos y un producto está en varios kits. Sin kits anidados: un
    componente nunca es kit."""

    kit = models.ForeignKey(Product, related_name="components", on_delete=models.CASCADE)
    component = models.ForeignKey(Product, related_name="used_in_kits", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        verbose_name = "Componente de kit"
        verbose_name_plural = "Componentes de kit"
        constraints = [
            models.UniqueConstraint(fields=["kit", "component"], name="unique_kit_component"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="kit_component_quantity_gt_0"),
        ]

    def clean(self):
        if self.component_id and self.component.is_kit:
            raise ValidationError("Un componente no puede ser un kit.")
        if self.kit_id and self.component_id and self.kit_id == self.component_id:
            raise ValidationError("Un kit no puede incluirse a sí mismo.")

    def __str__(self):
        return f"{self.kit.sku} ← {self.quantity} × {self.component.sku}"


class MarketplaceSku(models.Model):
    """Cómo se llama un producto de Pamo en un marketplace. Una fila por
    equivalencia (no una columna por canal): un producto puede tener varios
    SKU en el mismo marketplace. `ean` es el que asigna el marketplace
    (Sodimac), no un dato del producto."""

    product = models.ForeignKey(Product, related_name="marketplace_skus", on_delete=models.CASCADE)
    marketplace = models.CharField(max_length=32, choices=Marketplace.choices)
    sku = models.CharField(max_length=64)
    ean = models.CharField(max_length=20, blank=True)

    class Meta:
        verbose_name = "SKU de marketplace"
        verbose_name_plural = "SKUs de marketplace"
        constraints = [
            models.UniqueConstraint(fields=["marketplace", "sku"], name="unique_marketplace_sku"),
        ]

    def __str__(self):
        return f"{self.marketplace}:{self.sku} → {self.product.sku}"
