from django.urls import path

from .apis import InvoiceListAPI

urlpatterns = [
    path("", InvoiceListAPI.as_view(), name="invoice-list"),
]
