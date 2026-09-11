from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from config.constants import EXTERNAL_WRITES_ENABLED, SIIGO_INVOICE_WRITES_ENABLED
from logistics.models import Remittance
from logistics.serializers import RemittanceSerializer
from logistics.views import remittance_queryset, serialized

from .functions.build_invoice_preview import build_invoice_preview
from .functions.create_siigo_invoice import create_siigo_invoice
from .functions.ensure_invoice_lines import ensure_invoice_lines
from .models import RemittanceInvoiceAttempt

ACCOUNTING_ROLES = ["Admin", "Facturacion"]

InvoicePreviewSerializer = inline_serializer("InvoicePreview", {
    "remittance_id": serializers.UUIDField(),
    "remittance_number": serializers.CharField(allow_null=True),
    "customer": inline_serializer("InvoicePreviewCustomer", {
        "siigo_id": serializers.CharField(),
        "nit": serializers.CharField(),
        "name": serializers.CharField(),
    }),
    "payment_method": serializers.CharField(),
    "items": inline_serializer("InvoicePreviewItem", {
        "line_number": serializers.IntegerField(),
        "sku": serializers.CharField(),
        "description": serializers.CharField(),
        "quantity": serializers.DecimalField(max_digits=12, decimal_places=3),
        "unit_price": serializers.DecimalField(max_digits=14, decimal_places=2),
        "total": serializers.DecimalField(max_digits=14, decimal_places=2),
    }, many=True),
    "payment_days": serializers.IntegerField(),
    "subtotal": serializers.DecimalField(max_digits=14, decimal_places=2),
    "external_writes_enabled": serializers.BooleanField(),
})

InvoiceConfirmRequestSerializer = inline_serializer("InvoiceConfirmRequest", {
    "document_id": serializers.IntegerField(help_text="ID del comprobante/serie electrónica en Siigo."),
    "seller_id": serializers.IntegerField(help_text="ID del vendedor en Siigo."),
    "payment_type_id": serializers.IntegerField(help_text="ID de la forma de pago en Siigo."),
    "payment_days": serializers.IntegerField(required=False, default=15),
})

InvoiceConfirmResponseSerializer = inline_serializer("InvoiceConfirmResponse", {
    "status": serializers.ChoiceField(choices=RemittanceInvoiceAttempt.Status.choices),
    "external_invoice_id": serializers.CharField(),
    "external_number": serializers.CharField(),
})


@extend_schema(
    tags=["Accounting"],
    summary="Cola de contabilidad",
    description="Remisiones no anuladas con datos privados de preparación. Crea RemittanceInvoiceLine vacías "
    "para cualquier línea que aún no la tenga.",
    responses=RemittanceSerializer(many=True),
)
class RemittanceAccountingQueueAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request):
        queryset = remittance_queryset().exclude(current_state=Remittance.State.CANCELLED)
        remittances = list(queryset[:200])
        for remittance in remittances:
            ensure_invoice_lines(remittance)
        return Response(serialized(remittances, request, many=True))


@extend_schema(
    tags=["Accounting"],
    summary="Vista previa de factura",
    description="Valida que todas las líneas tengan siigo_sku/invoice_unit_price codificados en "
    "RemittanceInvoiceLine (vía /admin/) y devuelve el cálculo. No escribe nada en Siigo.",
    responses=InvoicePreviewSerializer,
)
class RemittanceInvoicePreviewAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(build_invoice_preview(remittance))


@extend_schema(
    tags=["Accounting"],
    summary="Emitir la factura en Siigo",
    description="Requiere EXTERNAL_WRITES_ENABLED y SIIGO_INVOICE_WRITES_ENABLED activas (503 si no). Idempotente "
    "por remisión+versión: reintentar devuelve el mismo resultado sin volver a llamar a Siigo. Si el intento "
    "anterior quedó en resultado desconocido (error de red), bloquea el reintento automático (400).",
    request=InvoiceConfirmRequestSerializer,
    responses={
        201: InvoiceConfirmResponseSerializer,
        400: InvoiceConfirmResponseSerializer,
        409: InvoiceConfirmResponseSerializer,
        502: InvoiceConfirmResponseSerializer,
        503: InvoiceConfirmResponseSerializer,
    },
)
class RemittanceInvoiceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def post(self, request, remittance_id):
        if not (EXTERNAL_WRITES_ENABLED and SIIGO_INVOICE_WRITES_ENABLED):
            return Response({
                "detail": "La emisión real a Siigo está desactivada en este ambiente.",
                "code": "EXTERNAL_WRITES_DISABLED",
            }, status=503)

        required = ["document_id", "seller_id", "payment_type_id"]
        missing = [field for field in required if not request.data.get(field)]
        if missing:
            return Response({field: "Este campo es obligatorio." for field in missing}, status=400)

        attempt = create_siigo_invoice(
            remittance_id,
            request.user,
            document_id=request.data["document_id"],
            seller_id=request.data["seller_id"],
            payment_type_id=request.data["payment_type_id"],
            payment_days=request.data.get("payment_days", 15),
        )
        return Response({
            "status": attempt.status,
            "external_invoice_id": attempt.external_invoice_id,
            "external_number": attempt.external_number,
        }, status=201)
