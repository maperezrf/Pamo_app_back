from django.urls import path

from .views import RemittanceAccountingQueueAPI, RemittanceInvoiceConfirmAPI, RemittanceInvoicePreviewAPI

urlpatterns = [
    path("remittances/", RemittanceAccountingQueueAPI.as_view(), name="remittance-accounting-queue"),
    path("remittances/<uuid:remittance_id>/invoice/preview/", RemittanceInvoicePreviewAPI.as_view(), name="remittance-invoice-preview"),
    path("remittances/<uuid:remittance_id>/invoice/confirm/", RemittanceInvoiceConfirmAPI.as_view(), name="remittance-invoice-confirm"),
]
