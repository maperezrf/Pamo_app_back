from django.contrib import admin

from .models import MarketplaceOrder, MarketplaceOrderItem


class MarketplaceOrderItemInline(admin.TabularInline):
    model = MarketplaceOrderItem
    extra = 0
    readonly_fields = ("marketplace_sku", "quantity", "unit_price", "shopify_variant_id")


@admin.register(MarketplaceOrder)
class MarketplaceOrderAdmin(admin.ModelAdmin):
    list_display = (
        "marketplace",
        "marketplace_order_number",
        "customer_identification",
        "status",
        "shopify_order_name",
        "updated_at",
    )
    list_filter = ("marketplace", "status")
    search_fields = ("marketplace_order_id", "marketplace_order_number", "customer_identification", "shopify_order_id")
    inlines = [MarketplaceOrderItemInline]
