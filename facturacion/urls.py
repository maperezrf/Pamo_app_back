from django.urls import path

from .views import (
    RemittanceConfirmAPI,
    RemittanceDetailAPI,
    RemittanceListCreateAPI,
    RemittanceSupplierSearchAPI,
    RemittancePrepareWhatsAppAPI,
    RemittanceReferenceDataAPI,
    RemittanceDocumentAPI,
    PublicRemittanceDocumentAPI,
    PublicRemittanceRecipientAPI,
    SupplierInvoiceDownloadAPI,
    SupplierInvoiceInterpretAPI,
    SupplierInvoiceUploadAPI,
)

urlpatterns = [
    path("remisiones/", RemittanceListCreateAPI.as_view(), name="remittance-list-create"),
    path("remisiones/referencias/", RemittanceReferenceDataAPI.as_view(), name="remittance-reference-data"),
    path("remisiones/proveedores/", RemittanceSupplierSearchAPI.as_view(), name="remittance-supplier-search"),
    path("remisiones/factura-proveedor/interpretar/", SupplierInvoiceInterpretAPI.as_view(), name="supplier-invoice-interpret"),
    path("remisiones/public/<str:token>/documento/", PublicRemittanceDocumentAPI.as_view(), name="public-remittance-document"),
    path("remisiones/public/<str:token>/", PublicRemittanceRecipientAPI.as_view(), name="public-remittance-recipient"),
    path("remisiones/<uuid:remittance_id>/documento/", RemittanceDocumentAPI.as_view(), name="remittance-document"),
    path("remisiones/<uuid:remittance_id>/", RemittanceDetailAPI.as_view(), name="remittance-detail"),
    path("remisiones/<uuid:remittance_id>/factura-proveedor/", SupplierInvoiceUploadAPI.as_view(), name="supplier-invoice-upload"),
    path("remisiones/<uuid:remittance_id>/factura-proveedor/<uuid:file_id>/", SupplierInvoiceDownloadAPI.as_view(), name="supplier-invoice-download"),
    path("remisiones/<uuid:remittance_id>/confirmar/", RemittanceConfirmAPI.as_view(), name="remittance-confirm"),
    path("remisiones/<uuid:remittance_id>/compartir-whatsapp/", RemittancePrepareWhatsAppAPI.as_view(), name="remittance-prepare-whatsapp"),
]
