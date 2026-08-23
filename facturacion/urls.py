from django.urls import path

from .views import (
    RemittanceAccountingQueueAPI,
    RemittanceConfirmAPI,
    RemittanceDetailAPI,
    RemittanceInvoiceConfirmAPI,
    RemittanceInvoicePreviewAPI,
    RemittanceListCreateAPI,
    RemittanceReferenceDataAPI,
)

urlpatterns = [
    path("remisiones/", RemittanceListCreateAPI.as_view(), name="remittance-list-create"),
    path("remisiones/referencias/", RemittanceReferenceDataAPI.as_view(), name="remittance-reference-data"),
    path("remisiones/contabilidad/", RemittanceAccountingQueueAPI.as_view(), name="remittance-accounting-queue"),
    path("remisiones/<uuid:remittance_id>/", RemittanceDetailAPI.as_view(), name="remittance-detail"),
    path("remisiones/<uuid:remittance_id>/confirmar/", RemittanceConfirmAPI.as_view(), name="remittance-confirm"),
    path("remisiones/<uuid:remittance_id>/factura/vista-previa/", RemittanceInvoicePreviewAPI.as_view(), name="remittance-invoice-preview"),
    path("remisiones/<uuid:remittance_id>/factura/confirmar/", RemittanceInvoiceConfirmAPI.as_view(), name="remittance-invoice-confirm"),
]
