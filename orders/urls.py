from django.urls import path
from .apis import GetOrdersTest

urlpatterns = [
    path("test/", GetOrdersTest.as_view(),name="orchestrator-process-types"),]
