"""Endpoints DRF del orquestador (montados en `config/urls.py` bajo
`/api/orchestrator/`).

Todos protegidos con `RoleRequiredMixin` (ver `docs/patterns/AUTHORIZATION.md`).
"""

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .models import ProcessExecution, ProcessScheduleConfig, ProcessType
from .serializers import (
    ProcessExecutionSerializer,
    ProcessScheduleConfigSerializer,
    ProcessTypeSerializer,
)
from .services import launch_process, request_cancellation


ALLOWED_ROLES = ["Admin", "Operaciones"]


class ProcessTypeListView(RoleRequiredMixin, APIView):
    """GET /process-types/ — catálogo de procesos disponibles."""

    allowed_roles = ALLOWED_ROLES

    def get(self, request):
        queryset = ProcessType.objects.filter(is_active=True)
        return Response(ProcessTypeSerializer(queryset, many=True).data)


class LaunchProcessView(RoleRequiredMixin, APIView):
    """POST /process-types/<code>/launch/ — lanza (o encola) una ejecución.

    Body opcional: {"params": {...}}. Responde 202 de inmediato; el
    consumidor debe hacer polling a GET /orchestrator/executions/{id}/.
    """

    allowed_roles = ALLOWED_ROLES

    def post(self, request, code):
        execution = launch_process(
            code=code, user=request.user, params=request.data.get("params", {})
        )
        return Response(
            status=202,
            data={"execution_id": execution.pk, "status": execution.status},
        )


class ProcessExecutionViewSet(RoleRequiredMixin, viewsets.ReadOnlyModelViewSet):
    """GET /executions/ y /executions/<id>/ — listado y detalle para polling.

    Filtros opcionales por query param: `status`, `process_type` (code).
    """

    allowed_roles = ALLOWED_ROLES
    serializer_class = ProcessExecutionSerializer
    queryset = ProcessExecution.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        status_param = self.request.query_params.get("status")
        process_type = self.request.query_params.get("process_type")
        if status_param:
            queryset = queryset.filter(status=status_param)
        if process_type:
            queryset = queryset.filter(process_type__code=process_type)
        return queryset

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """POST /executions/<id>/cancel/ — cancelación cooperativa, sin body."""
        execution = self.get_object()
        execution = request_cancellation(execution)
        return Response(ProcessExecutionSerializer(execution).data)


class ProcessScheduleConfigViewSet(RoleRequiredMixin, viewsets.ModelViewSet):
    """CRUD de programaciones (`/schedules/`)."""

    allowed_roles = ALLOWED_ROLES
    serializer_class = ProcessScheduleConfigSerializer
    queryset = ProcessScheduleConfig.objects.all()

    def perform_create(self, serializer):
        from .core.scheduler import register_schedule

        schedule = serializer.save(created_by=self.request.user)
        register_schedule(schedule)

    def perform_update(self, serializer):
        from .core.scheduler import register_schedule

        schedule = serializer.save()
        register_schedule(schedule)

    def perform_destroy(self, instance):
        from .core.scheduler import unregister_schedule

        unregister_schedule(instance.pk)
        instance.delete()
