from django.contrib import admin

from .models import KitComponent, MarketplaceSku, Product


class MarketplaceSkuInline(admin.TabularInline):
    model = MarketplaceSku
    extra = 0


class KitComponentInline(admin.TabularInline):
    model = KitComponent
    fk_name = "kit"
    extra = 0
    autocomplete_fields = ("component",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "is_kit")
    list_filter = ("is_kit",)
    search_fields = ("sku", "name", "marketplace_skus__sku")
    inlines = [MarketplaceSkuInline, KitComponentInline]


@admin.register(MarketplaceSku)
class MarketplaceSkuAdmin(admin.ModelAdmin):
    list_display = ("marketplace", "sku", "ean", "product")
    list_filter = ("marketplace",)
    search_fields = ("sku", "ean", "product__sku")
    autocomplete_fields = ("product",)
