from django.urls import path

from .apis import (
    EquivalencesAPI,
    KitDetailAPI,
    KitsAPI,
    MarketplaceSkuDetailAPI,
    ProductListAPI,
    SkuUploadDetailAPI,
    SkuUploadListAPI,
)

urlpatterns = [
    path("", ProductListAPI.as_view(), name="products-list"),
    path("equivalences/", EquivalencesAPI.as_view(), name="products-equivalences"),
    path("kits/", KitsAPI.as_view(), name="products-kits"),
    path("kits/<int:product_id>/", KitDetailAPI.as_view(), name="products-kit-detail"),
    path(
        "marketplace-skus/<int:equivalence_id>/",
        MarketplaceSkuDetailAPI.as_view(),
        name="products-marketplace-sku-detail",
    ),
    path("sku-uploads/", SkuUploadListAPI.as_view(), name="products-sku-uploads"),
    path("sku-uploads/<int:upload_id>/", SkuUploadDetailAPI.as_view(), name="products-sku-upload-detail"),
]
