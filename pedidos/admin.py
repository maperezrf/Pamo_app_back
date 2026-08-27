from django.contrib import admin

from .models import (
    IntegrationStatus,
    LogisticsAudit,
    ManualFollowup,
    MessagingConfig,
    MessagingContact,
    Order,
    OrderItem,
    SavedFilter,
    Shipment,
    ShipmentDocument,
    ShipmentItem,
    TrackingEvent,
    WarehouseLocation,
)


for model in (
    WarehouseLocation,
    Order,
    OrderItem,
    Shipment,
    ShipmentItem,
    TrackingEvent,
    LogisticsAudit,
    ShipmentDocument,
    MessagingConfig,
    MessagingContact,
    ManualFollowup,
    SavedFilter,
    IntegrationStatus,
):
    admin.site.register(model)

