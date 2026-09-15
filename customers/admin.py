from django.contrib import admin

from .models import ShopifyCustomer


@admin.register(ShopifyCustomer)
class ShopifyCustomerAdmin(admin.ModelAdmin):
    list_display = ("shopify_id", "identification", "first_name", "last_name", "email", "phone", "synced_at")
    search_fields = ("shopify_id", "identification", "email", "phone", "first_name", "last_name")
    list_filter = ("synced_at",)
