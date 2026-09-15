from django.urls import path

from .webhooks import ShopifyCustomerWebhookView

urlpatterns = [
    path("webhooks/shopify/customer/", ShopifyCustomerWebhookView.as_view(), name="customers-shopify-webhook"),
]
