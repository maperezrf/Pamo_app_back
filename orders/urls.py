from django.urls import path
from .apis import OrderListAPI
from .webhooks import MadecentroOrderWebhookView, MercadoLibreOrderWebhookView, ShopifyOrderWebhookView

urlpatterns = [
    path("", OrderListAPI.as_view(), name="orders-list"),
    path("webhooks/mercadolibre/",MercadoLibreOrderWebhookView.as_view(), name="orders-mercadolibre-webhook",),
    path("webhooks/shopify/",ShopifyOrderWebhookView.as_view(), name="orders-shopify-webhook",),
    path("webhooks/madecentro/", MadecentroOrderWebhookView.as_view(), name="orders-madecentro-webhook"),
]
