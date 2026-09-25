from django.urls import path
from .apis import GetOrdersTest
from .webhooks import MadecentroOrderWebhookView, MercadoLibreOrderWebhookView

urlpatterns = [
    path("test/", GetOrdersTest.as_view(),name="orchestrator-process-types"),
    path(
        "webhooks/mercadolibre/",
        MercadoLibreOrderWebhookView.as_view(),
        name="orders-mercadolibre-webhook",
    ),
    path(
        "webhooks/madecentro/",
        MadecentroOrderWebhookView.as_view(),
        name="orders-madecentro-webhook",
    ),
]
