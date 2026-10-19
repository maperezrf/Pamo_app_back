from django.urls import path
from .apis import (
    DispatchFetchLabelAPI,
    DispatchLabelFileAPI,
    DispatchLabelLinkAPI,
    DispatchGenerateLabelAPI,
    DispatchNotifyAPI,
    DispatchQuoteLabelAPI,
    DispatchReleaseLabelAPI,
    DispatchUploadLabelAPI,
    OrderChannelLabelFileAPI,
    OrderChannelLabelRequestAPI,
    OrderChannelLabelUploadAPI,
    OrderListAPI,
    SodimacRpaCallbackAPI,
    SodimacRpaControlAPI,
    SodimacRpaStatusAPI,
    SodimacRpaStockDispatchAPI,
)
from .functions.sodimac_rpa import rpa_continue, rpa_kill
from .webhooks import MadecentroOrderWebhookView, MercadoLibreOrderWebhookView, ShopifyOrderWebhookView

urlpatterns = [
    path("", OrderListAPI.as_view(), name="orders-list"),
    path("labels/<str:token>/", DispatchLabelLinkAPI.as_view(), name="orders-dispatch-label-link"),
    path("sodimac/rpa/callback/", SodimacRpaCallbackAPI.as_view(), name="orders-sodimac-rpa-callback"),
    path("sodimac/rpa/status/", SodimacRpaStatusAPI.as_view(), name="orders-sodimac-rpa-status"),
    path(
        "sodimac/rpa/continue/",
        SodimacRpaControlAPI.as_view(action=rpa_continue),
        name="orders-sodimac-rpa-continue",
    ),
    path("sodimac/rpa/kill/", SodimacRpaControlAPI.as_view(action=rpa_kill), name="orders-sodimac-rpa-kill"),
    path(
        "sodimac/rpa/orders/<str:purchase_order>/stock-dispatch/",
        SodimacRpaStockDispatchAPI.as_view(),
        name="orders-sodimac-rpa-stock-dispatch",
    ),
    path(
        "<str:shopify_order_id>/channel-label/request/",
        OrderChannelLabelRequestAPI.as_view(),
        name="orders-channel-label-request",
    ),
    path(
        "<str:shopify_order_id>/channel-label/upload/",
        OrderChannelLabelUploadAPI.as_view(),
        name="orders-channel-label-upload",
    ),
    path(
        "<str:shopify_order_id>/channel-label/<str:kind>/",
        OrderChannelLabelFileAPI.as_view(),
        name="orders-channel-label-file",
    ),
    path("<str:shopify_order_id>/dispatch/notify/", DispatchNotifyAPI.as_view(), name="orders-dispatch-notify"),
    path("<str:shopify_order_id>/dispatch/label/fetch/", DispatchFetchLabelAPI.as_view(), name="orders-dispatch-label-fetch"),
    path("<str:shopify_order_id>/dispatch/label/quote/", DispatchQuoteLabelAPI.as_view(), name="orders-dispatch-label-quote"),
    path("<str:shopify_order_id>/dispatch/label/file/", DispatchLabelFileAPI.as_view(), name="orders-dispatch-label-file"),
    path("<str:shopify_order_id>/dispatch/label/release/", DispatchReleaseLabelAPI.as_view(), name="orders-dispatch-label-release"),
    path("<str:shopify_order_id>/dispatch/label/upload/", DispatchUploadLabelAPI.as_view(), name="orders-dispatch-label-upload"),
    path("<str:shopify_order_id>/dispatch/label/", DispatchGenerateLabelAPI.as_view(), name="orders-dispatch-label"),
    path("webhooks/mercadolibre/",MercadoLibreOrderWebhookView.as_view(), name="orders-mercadolibre-webhook",),
    path("webhooks/shopify/",ShopifyOrderWebhookView.as_view(), name="orders-shopify-webhook",),
    path("webhooks/madecentro/", MadecentroOrderWebhookView.as_view(), name="orders-madecentro-webhook"),
]
