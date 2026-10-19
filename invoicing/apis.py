from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .functions.list_invoices import filter_invoices, format_invoices
from .serializers import InvoiceListQuerySerializer

# Mismos roles que el listado de pedidos (`orders.apis.ORDERS_LIST_ROLES`).
INVOICES_LIST_ROLES = ["Admin", "Operaciones"]


class InvoicePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


class InvoiceListAPI(RoleRequiredMixin, APIView):
    """Facturas en Siigo de los pedidos de marketplace, con su estado (y el
    motivo si falló), valores, SKU y los números para enlazar al pedido de
    Shopify y a la factura en Siigo. Solo lee la base de datos."""

    allowed_roles = INVOICES_LIST_ROLES

    def get(self, request):
        serializer = InvoiceListQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data

        paginator = InvoicePagination()
        page = paginator.paginate_queryset(
            filter_invoices(
                status=params.get("status"),
                marketplace=params.get("marketplace"),
                date_from=params.get("date_from"),
                date_to=params.get("date_to"),
                search=params["search"],
            ),
            request,
            view=self,
        )
        return Response(
            {
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "invoices": format_invoices(page),
            }
        )
