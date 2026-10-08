from django.urls import path
from .apis import (
    DispatchFetchLabelAPI,
    DispatchGenerateLabelAPI,
    DispatchNotifyAPI,
    DispatchQuoteLabelAPI,
    OrderListAPI,
)
from .webhooks import MadecentroOrderWebhookView, MercadoLibreOrderWebhookView, ShopifyOrderWebhookView

urlpatterns = [
    path("", OrderListAPI.as_view(), name="orders-list"),
    path("<str:shopify_order_id>/dispatch/notify/", DispatchNotifyAPI.as_view(), name="orders-dispatch-notify"),
    path("<str:shopify_order_id>/dispatch/label/fetch/", DispatchFetchLabelAPI.as_view(), name="orders-dispatch-label-fetch"),
    path("<str:shopify_order_id>/dispatch/label/quote/", DispatchQuoteLabelAPI.as_view(), name="orders-dispatch-label-quote"),
    path("<str:shopify_order_id>/dispatch/label/", DispatchGenerateLabelAPI.as_view(), name="orders-dispatch-label"),
    path("webhooks/mercadolibre/",MercadoLibreOrderWebhookView.as_view(), name="orders-mercadolibre-webhook",),
    path("webhooks/shopify/",ShopifyOrderWebhookView.as_view(), name="orders-shopify-webhook",),
    path("webhooks/madecentro/", MadecentroOrderWebhookView.as_view(), name="orders-madecentro-webhook"),
]
