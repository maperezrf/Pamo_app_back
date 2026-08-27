from django.urls import path

from .views import (
    BulkPilotSimulationAPI,
    CatalogColumnOptionsAPI,
    CatalogChannelRefreshAPI,
    CatalogWorkspaceAPI,
    ChannelAlignmentAPI,
    EnviaQuoteContractAPI,
    ExecutiveSimulationAPI,
    HypothesisPolicyAPI,
    Phase6MultwarehouseAPI,
    Phase6PricingAPI,
    Phase6WorkspaceAPI,
    Phase7WorkspaceAPI,
    SodimacCatalogWorkspaceAPI,
    PhysicalMeasurementTemplateAPI,
    PhysicalMeasurementWorkspaceAPI,
    PhysicalReviewQueueAPI,
    PricingSimulationAPI,
    ShopifyImportPlanAPI,
)


urlpatterns = [
    path("workspace/", CatalogWorkspaceAPI.as_view(), name="catalog-workspace"),
    path("workspace/refresh-channels/", CatalogChannelRefreshAPI.as_view(), name="catalog-refresh-channels"),
    path("workspace/column-options/", CatalogColumnOptionsAPI.as_view(), name="catalog-column-options"),
    path("alignment/", ChannelAlignmentAPI.as_view(), name="catalog-alignment"),
    path("pricing/simulate/", PricingSimulationAPI.as_view(), name="pricing-simulate"),
    path("pricing/hypothesis/", HypothesisPolicyAPI.as_view(), name="pricing-hypothesis"),
    path("shopify/import-plan/", ShopifyImportPlanAPI.as_view(), name="shopify-import-plan"),
    path("executive/simulation/", ExecutiveSimulationAPI.as_view(), name="executive-simulation"),
    path("pilot/simulation/", BulkPilotSimulationAPI.as_view(), name="pilot-simulation"),
    path("physical/review-queue/", PhysicalReviewQueueAPI.as_view(), name="physical-review-queue"),
    path("physical/measurement-workspace/", PhysicalMeasurementWorkspaceAPI.as_view(), name="physical-measurement-workspace"),
    path("physical/measurement-template/", PhysicalMeasurementTemplateAPI.as_view(), name="physical-measurement-template"),
    path("envia/quote-contract/", EnviaQuoteContractAPI.as_view(), name="envia-quote-contract"),
    path("phase6/workspace/", Phase6WorkspaceAPI.as_view(), name="phase6-workspace"),
    path("phase6/pricing/", Phase6PricingAPI.as_view(), name="phase6-pricing"),
    path("phase6/multwarehouse/", Phase6MultwarehouseAPI.as_view(), name="phase6-multwarehouse"),
    path("phase7/workspace/", Phase7WorkspaceAPI.as_view(), name="phase7-workspace"),
    path("sodimac/workspace/", SodimacCatalogWorkspaceAPI.as_view(), name="sodimac-catalog-workspace"),
]
