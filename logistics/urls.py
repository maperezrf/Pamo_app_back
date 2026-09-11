from django.urls import path

from .views import (
    RemittanceCancelAPI,
    RemittanceConfirmAPI,
    RemittanceDetailAPI,
    RemittanceListCreateAPI,
    RemittanceReferenceDataAPI,
    RemittanceSendToConfirmAPI,
)

urlpatterns = [
    path("remittances/", RemittanceListCreateAPI.as_view(), name="remittance-list-create"),
    path("remittances/references/", RemittanceReferenceDataAPI.as_view(), name="remittance-reference-data"),
    path("remittances/<uuid:remittance_id>/", RemittanceDetailAPI.as_view(), name="remittance-detail"),
    path("remittances/<uuid:remittance_id>/send-to-confirm/", RemittanceSendToConfirmAPI.as_view(), name="remittance-send-to-confirm"),
    path("remittances/<uuid:remittance_id>/confirm/", RemittanceConfirmAPI.as_view(), name="remittance-confirm"),
    path("remittances/<uuid:remittance_id>/cancel/", RemittanceCancelAPI.as_view(), name="remittance-cancel"),
]
