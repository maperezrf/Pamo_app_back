from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from config.constants import EXTERNAL_WRITES_ENABLED, SIIGO_INVOICE_WRITES_ENABLED

from .functions.build_invoice_preview import build_invoice_preview
from .functions.confirm_remittance import confirm_remittance
from .models import Remittance, RemittanceFavorite, RemittanceLine, RemittanceWarehouse
from .serializers import FavoriteSerializer, RemittanceSerializer, WarehouseSerializer


OPERATIONS_ROLES = ["Admin", "Operaciones", "Logistica"]
ACCOUNTING_ROLES = ["Admin", "Facturacion"]


def remittance_queryset():
    return Remittance.objects.select_related("warehouse", "supplier", "customer", "delivery").prefetch_related(
        Prefetch("lines", queryset=RemittanceLine.objects.order_by("line_number"))
    )


def serialized(remittance_or_queryset, request, *, many=False, accounting=False):
    include_accounting = accounting or request.user.is_superuser or request.user.groups.filter(name="Facturacion").exists()
    return RemittanceSerializer(
        remittance_or_queryset,
        many=many,
        context={"request": request, "include_accounting": include_accounting},
    ).data


class RemittanceListCreateAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        queryset = remittance_queryset()
        status_filter = request.query_params.get("invoice_status")
        if status_filter:
            queryset = queryset.filter(invoice_status=status_filter)
        return Response(serialized(queryset[:200], request, many=True))

    def post(self, request):
        serializer = RemittanceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        remittance = serializer.save()
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request), status=201)


class RemittanceDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Operaciones", "Logistica", "Facturacion"]

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(serialized(remittance, request))


class RemittanceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        expected_version = request.data.get("expected_version")
        if not isinstance(expected_version, int):
            return Response({"expected_version": "Envía la versión esperada."}, status=400)
        remittance = confirm_remittance(remittance_id, expected_version, request.user)
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request))


class RemittanceReferenceDataAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        favorites = RemittanceFavorite.objects.filter(is_active=True).select_related("party")
        warehouses = RemittanceWarehouse.objects.filter(is_active=True)
        return Response({
            "warehouses": WarehouseSerializer(warehouses, many=True).data,
            "favorites": FavoriteSerializer(favorites, many=True).data,
        })


class RemittanceAccountingQueueAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request):
        queryset = remittance_queryset().exclude(document_status=Remittance.DocumentStatus.CANCELLED)
        return Response(serialized(queryset[:200], request, many=True, accounting=True))


class RemittanceInvoicePreviewAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(build_invoice_preview(remittance))


class RemittanceInvoiceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def post(self, request, remittance_id):
        if not (EXTERNAL_WRITES_ENABLED and SIIGO_INVOICE_WRITES_ENABLED):
            return Response({
                "detail": "La emisión real a Siigo está desactivada en este ambiente.",
                "code": "EXTERNAL_WRITES_DISABLED",
            }, status=503)
        return Response({
            "detail": "El adaptador de emisión Siigo se migrará en la fase financiera posterior.",
            "code": "SIIGO_ADAPTER_NOT_MIGRATED",
        }, status=501)
