from django.urls import path
from .apis import GetOrdersTest
from .webhooks import MercadoLibreOrderWebhookView

urlpatterns = [
    path("test/", GetOrdersTest.as_view(),name="orchestrator-process-types"),
    path(
        "webhooks/mercadolibre/",
        MercadoLibreOrderWebhookView.as_view(),
        name="orders-mercadolibre-webhook",
    ),
]
