from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .apis import (
    LaunchProcessView,
    ProcessExecutionViewSet,
    ProcessScheduleConfigViewSet,
    ProcessTypeListView,
)

router = DefaultRouter()
router.register(r"executions", ProcessExecutionViewSet,
                basename="orchestrator-execution")
router.register(r"schedules", ProcessScheduleConfigViewSet,
                basename="orchestrator-schedule")

urlpatterns = [
    path("process-types/", ProcessTypeListView.as_view(),
         name="orchestrator-process-types"),
    path(
        "process-types/<str:code>/launch/",
        LaunchProcessView.as_view(),
        name="orchestrator-launch-process",
    ),
    path("", include(router.urls)),
]
