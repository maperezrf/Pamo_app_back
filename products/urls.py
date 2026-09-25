from django.urls import path

from .apis import EquivalencesAPI, KitsAPI, ProductListAPI

urlpatterns = [
    path("", ProductListAPI.as_view(), name="products-list"),
    path("equivalences/", EquivalencesAPI.as_view(), name="products-equivalences"),
    path("kits/", KitsAPI.as_view(), name="products-kits"),
]
