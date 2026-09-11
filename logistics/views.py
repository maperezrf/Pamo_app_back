from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .functions.cancel_remittance import cancel_remittance
from .functions.confirm_remittance import confirm_remittance
from .functions.send_remittance_to_confirm import send_remittance_to_confirm
from .models import Remittance, RemittanceFavorite, RemittanceLine, RemittanceWarehouse
from .serializers import FavoriteSerializer, RemittanceSerializer, WarehouseSerializer


OPERATIONS_ROLES = ["Admin", "Operaciones", "Logistica"]
ACCOUNTING_ROLES = ["Admin", "Facturacion"]

ExpectedVersionSerializer = inline_serializer("ExpectedVersion", {"expected_version": serializers.IntegerField()})
CancelReasonSerializer = inline_serializer("CancelReason", {"reason": serializers.CharField()})
ReferenceDataSerializer = inline_serializer("RemittanceReferenceData", {
    "warehouses": WarehouseSerializer(many=True),
    "favorites": FavoriteSerializer(many=True),
})


def remittance_queryset():
    return Remittance.objects.select_related("warehouse", "supplier", "customer", "delivery").prefetch_related(
        Prefetch("lines", queryset=RemittanceLine.objects.order_by("line_number")),
        "states",
    )


def serialized(remittance_or_queryset, request, *, many=False):
    include_accounting = (
        request.user.is_superuser or request.user.groups.filter(name__in=["Admin", "Facturacion"]).exists()
    )
    return RemittanceSerializer(
        remittance_or_queryset, many=many, context={"request": request, "include_accounting": include_accounting},
    ).data


@extend_schema(tags=["Logistics"])
class RemittanceListCreateAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    @extend_schema(
        summary="Listar remisiones",
        parameters=[OpenApiParameter(
            name="current_state", type=str, location=OpenApiParameter.QUERY, required=False,
            description="Filtra por Remittance.State (DRAFT, SENT_TO_CONFIRM, CONFIRMED, PENDING_INVOICE, "
            "INVOICING, INVOICED, INVOICE_FAILED, CANCELLED).",
        )],
        responses=RemittanceSerializer(many=True),
    )
    def get(self, request):
        queryset = remittance_queryset()
        state_filter = request.query_params.get("current_state")
        if state_filter:
            queryset = queryset.filter(current_state=state_filter)
        return Response(serialized(queryset[:200], request, many=True))

    @extend_schema(
        summary="Crear una remisión (borrador)",
        request=RemittanceSerializer,
        responses={201: RemittanceSerializer},
    )
    def post(self, request):
        serializer = RemittanceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        remittance = serializer.save()
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request), status=201)


@extend_schema(
    tags=["Logistics"],
    summary="Detalle de una remisión",
    responses=RemittanceSerializer,
)
class RemittanceDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Operaciones", "Logistica", "Facturacion"]

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(serialized(remittance, request))


@extend_schema(
    tags=["Logistics"],
    summary="Enviar a confirmar (DRAFT -> SENT_TO_CONFIRM)",
    description="Marca que la remisión se envió al cliente para firma/confirmación. Hoy es marcación manual.",
    request=None,
    responses=RemittanceSerializer,
)
class RemittanceSendToConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        remittance = send_remittance_to_confirm(remittance_id, request.user)
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request))


@extend_schema(
    tags=["Logistics"],
    summary="Confirmar (SENT_TO_CONFIRM -> CONFIRMED -> PENDING_INVOICE)",
    description="El cliente ya firmó/confirmó. Asigna el consecutivo RD-xxxx de forma transaccional.",
    request=ExpectedVersionSerializer,
    responses=RemittanceSerializer,
)
class RemittanceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        expected_version = request.data.get("expected_version")
        if not isinstance(expected_version, int):
            return Response({"expected_version": "Envía la versión esperada."}, status=400)
        remittance = confirm_remittance(remittance_id, expected_version, request.user)
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request))


@extend_schema(
    tags=["Logistics"],
    summary="Anular (-> CANCELLED)",
    description="Solo permitido desde DRAFT, SENT_TO_CONFIRM o CONFIRMED -- nunca después de INVOICED.",
    request=CancelReasonSerializer,
    responses=RemittanceSerializer,
)
class RemittanceCancelAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        reason = request.data.get("reason", "")
        remittance = cancel_remittance(remittance_id, request.user, reason)
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request))


@extend_schema(
    tags=["Logistics"],
    summary="Bodegas y favoritos activos",
    responses=ReferenceDataSerializer,
)
class RemittanceReferenceDataAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        favorites = RemittanceFavorite.objects.filter(is_active=True).select_related("party")
        warehouses = RemittanceWarehouse.objects.filter(is_active=True)
        return Response({
            "warehouses": WarehouseSerializer(warehouses, many=True).data,
            "favorites": FavoriteSerializer(favorites, many=True).data,
        })
