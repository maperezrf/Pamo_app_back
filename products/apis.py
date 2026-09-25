from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .functions.export_equivalences import export_equivalences
from .functions.export_kits import export_kits
from .functions.import_equivalences import InvalidColumnsError, import_equivalences
from .functions.import_kits import import_kits
from .models import KitComponent, Product
from .serializers import CatalogRowsSerializer, ProductSerializer

CATALOG_ROLES = ["Admin"]


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
