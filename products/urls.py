from django.urls import path

from .apis import EquivalencesAPI, KitsAPI, ProductListAPI, SkuUploadDetailAPI, SkuUploadListAPI

urlpatterns = [
    path("", ProductListAPI.as_view(), name="products-list"),
    path("equivalences/", EquivalencesAPI.as_view(), name="products-equivalences"),
    path("kits/", KitsAPI.as_view(), name="products-kits"),
    path("sku-uploads/", SkuUploadListAPI.as_view(), name="products-sku-uploads"),
    path("sku-uploads/<int:upload_id>/", SkuUploadDetailAPI.as_view(), name="products-sku-upload-detail"),
]
