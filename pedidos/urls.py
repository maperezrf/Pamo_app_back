from django.urls import path

from .views import (
    FilterOptionsAPI,
    IntegrationsAPI,
    LocalSyncShieldAPI,
    LocationsAPI,
    ManualMessagingAPI,
    ManualMessagingDetailAPI,
    MessagingConfigsAPI,
    OrderDetailAPI,
    OrdersListAPI,
    OrdersOverviewAPI,
    SavedFilterDetailAPI,
    SavedFiltersAPI,
    ShipmentAPI,
    ShipmentDocumentAPI,
    ShipmentIncidentAPI,
    SupplierResponseSimulationAPI,
)


urlpatterns = [
    path("", OrdersListAPI.as_view(), name="pedidos-list"),
    path("overview/", OrdersOverviewAPI.as_view(), name="pedidos-overview"),
    path("locations/", LocationsAPI.as_view(), name="pedidos-locations"),
    path("filter-options/", FilterOptionsAPI.as_view(), name="pedidos-filter-options"),
    path("integrations/", IntegrationsAPI.as_view(), name="pedidos-integrations"),
    path("sync/<str:provider>/", LocalSyncShieldAPI.as_view(), name="pedidos-sync-shield"),
    path("saved-filters/", SavedFiltersAPI.as_view(), name="pedidos-saved-filters"),
    path("saved-filters/<int:filter_id>/", SavedFilterDetailAPI.as_view(), name="pedidos-saved-filter-detail"),
    path("messaging/configs/", MessagingConfigsAPI.as_view(), name="pedidos-messaging-configs"),
    path("messaging/manual/", ManualMessagingAPI.as_view(), name="pedidos-messaging-manual"),
    path(
        "messaging/manual/<uuid:followup_id>/<str:action>/",
        ManualMessagingDetailAPI.as_view(),
        name="pedidos-messaging-manual-detail",
    ),
    path("shipments/<uuid:shipment_id>/", ShipmentAPI.as_view(), name="pedidos-shipment"),
    path(
        "shipments/<uuid:shipment_id>/incident/",
        ShipmentIncidentAPI.as_view(),
        name="pedidos-shipment-incident",
    ),
    path(
        "shipments/<uuid:shipment_id>/document/",
        ShipmentDocumentAPI.as_view(),
        name="pedidos-shipment-document",
    ),
    path(
        "shipments/<uuid:shipment_id>/supplier-response/simulate/",
        SupplierResponseSimulationAPI.as_view(),
        name="pedidos-supplier-response-simulate",
    ),
    path("<uuid:order_id>/", OrderDetailAPI.as_view(), name="pedidos-detail"),
]
