from django.contrib import admin

from .models import MarketplaceOrder, MarketplaceOrderItem


class MarketplaceOrderItemInline(admin.TabularInline):
    model = MarketplaceOrderItem
    extra = 0
    # `inventory_snapshot`: stock por bodega que se evaluó al elegir la
    # bodega del pedido -- lo que necesita ver quien resuelve una novedad.
    readonly_fields = ("marketplace_sku", "quantity", "unit_price", "shopify_variant_id", "inventory_snapshot")


@admin.register(MarketplaceOrder)
class MarketplaceOrderAdmin(admin.ModelAdmin):
    list_display = (
        "marketplace",
        "marketplace_order_number",
        "customer_identification",
        "status",
        "shopify_order_name",
        "fulfillment_status",
        "fulfillment_location_name",
        "updated_at",
    )
    list_filter = ("marketplace", "status", "fulfillment_status")
    search_fields = ("marketplace_order_id", "marketplace_order_number", "customer_identification", "shopify_order_id")
    inlines = [MarketplaceOrderItemInline]
    # Una novedad se resuelve a mano: elegir fulfillment_location_id/_name,
    # pasar fulfillment_status a "resuelta_manual" y dejar la nota. El
    # proceso no vuelve a tocar un pedido resuelto manualmente.
