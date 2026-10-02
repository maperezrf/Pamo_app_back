from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from orchestrator.services import get_execution_status, launch_process

from .functions.export_equivalences import export_equivalences
from .functions.export_kits import export_kits
from .functions.import_equivalences import InvalidColumnsError, import_equivalences
from .functions.import_kits import import_kits
from .models import KitComponent, Product, SkuUpload
from .serializers import CatalogRowsSerializer, ProductSerializer, SkuUploadCreateSerializer

CATALOG_ROLES = ["Admin"]
# Separado de CATALOG_ROLES: los roles nuevos que puedan cargar SKU se
# agregan aquí cuando se definan.
SKU_UPLOAD_ROLES = ["Admin"]
SKU_UPLOAD_PROCESS = "products.upload_sku_equivalences"


class ProductPagination(PageNumberPagination):
    page_size = 100


class ProductListAPI(RoleRequiredMixin, APIView):
    """Catálogo paginado con sus equivalencias y, si es kit, sus
    componentes. `?search=` filtra por SKU de Pamo o de marketplace."""

    allowed_roles = CATALOG_ROLES

    def get(self, request):
        products = Product.objects.prefetch_related(
            "marketplace_skus",
            Prefetch("components", queryset=KitComponent.objects.select_related("component")),
        ).order_by("sku")
        search = request.query_params.get("search", "").strip()
        if search:
            products = products.filter(
                Q(sku__icontains=search) | Q(marketplace_skus__sku__icontains=search)
            ).distinct()

        paginator = ProductPagination()
        page = paginator.paginate_queryset(products, request, view=self)
        return paginator.get_paginated_response(ProductSerializer(page, many=True).data)


class _CatalogRowsAPI(RoleRequiredMixin, APIView):
    """GET descarga las filas (el frontend arma el Excel); POST carga las
    filas que el frontend leyó del Excel. La lógica vive en
    `products/functions/`."""

    allowed_roles = CATALOG_ROLES
    export_function = None
    import_function = None

    def get(self, request):
        return Response(self.export_function())

    def post(self, request):
        serializer = CatalogRowsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = self.import_function(serializer.validated_data["rows"])
        except InvalidColumnsError as error:
            return Response(
                {"detail": str(error), "unknown_columns": error.columns},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)


class EquivalencesAPI(_CatalogRowsAPI):
    export_function = staticmethod(export_equivalences)
    import_function = staticmethod(import_equivalences)


class KitsAPI(_CatalogRowsAPI):
    export_function = staticmethod(export_kits)
    import_function = staticmethod(import_kits)


class SkuUploadPagination(PageNumberPagination):
    page_size = 20


def _sku_upload_data(upload, include_rows):
    """Datos de una carga con el estado de su ejecución (única fuente del
    estado y el progreso: el orquestador)."""
    execution = get_execution_status(upload.execution_id) if upload.execution_id else None
    execution = execution or {}
    data = {
        "id": upload.pk,
        "marketplace": upload.marketplace,
        "uploaded_by": upload.uploaded_by.email if upload.uploaded_by else None,
        "created_at": upload.created_at,
        "finished_at": upload.finished_at,
        "status": execution.get("status"),
        "progress_percent": execution.get("progress_percent"),
        "current_step": execution.get("current_step"),
        "error_message": execution.get("error_message"),
        "summary": upload.summary,
    }
    if include_rows:
        data["rows"] = upload.results
    return data


class SkuUploadListAPI(RoleRequiredMixin, APIView):
    """POST lanza una carga de equivalencias de SKU de un marketplace, en
    segundo plano; GET es el historial paginado (todas las cargas: el
    catálogo es compartido)."""

    allowed_roles = SKU_UPLOAD_ROLES

    def post(self, request):
        serializer = SkuUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = SkuUpload.objects.create(
            marketplace=serializer.validated_data["marketplace"],
            rows=serializer.validated_data["rows"],
            uploaded_by=request.user,
        )
        execution = launch_process(code=SKU_UPLOAD_PROCESS, user=request.user, params={"upload_id": upload.pk})
        upload.execution_id = execution.pk
        upload.save(update_fields=["execution_id"])
        return Response({"id": upload.pk, "execution_id": execution.pk}, status=status.HTTP_202_ACCEPTED)

    def get(self, request):
        uploads = SkuUpload.objects.select_related("uploaded_by").order_by("-created_at")
        paginator = SkuUploadPagination()
        page = paginator.paginate_queryset(uploads, request, view=self)
        return paginator.get_paginated_response([_sku_upload_data(upload, include_rows=False) for upload in page])


class SkuUploadDetailAPI(RoleRequiredMixin, APIView):
    """Detalle de una carga para el polling del frontend. `rows` (filas de
    entrada con `resultado` y `resultado_codigo`) es `null` hasta que
    termina."""

    allowed_roles = SKU_UPLOAD_ROLES

    def get(self, request, upload_id):
        upload = SkuUpload.objects.select_related("uploaded_by").filter(pk=upload_id).first()
        if upload is None:
            return Response({"detail": "No existe."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_sku_upload_data(upload, include_rows=True))
