from django.urls import path

from .mercadolibre.apis import MercadoLibreCallbackView, MercadoLibreConnectView

urlpatterns = [
    path("mercadolibre/connect/", MercadoLibreConnectView.as_view(), name="mercadolibre-connect"),
    path("mercadolibre/callback/", MercadoLibreCallbackView.as_view(), name="mercadolibre-callback"),
]
