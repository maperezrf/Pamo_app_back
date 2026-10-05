from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .functions.list_orders import filter_orders, format_orders
from .functions.list_orders_not_created import list_orders_not_created
from .models import ShopifyOrderSyncState
from .serializers import OrderListQuerySerializer

# Supuesto del plan (shopify-orders-listing.md): los mismos roles del
# orquestador. Cambiar aquí si el negocio define otro.
ORDERS_LIST_ROLES = ["Admin", "Operaciones"]


class OrderPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


class OrderListAPI(RoleRequiredMixin, APIView):
    """Pedidos de la copia local de Shopify (paginados, con el comprador
    real) y, aparte, los pedidos de marketplace que no se pudieron crear.
    Solo lee la base de datos del proyecto; la copia la mantienen el webhook
    y la reconciliación de pedidos de Shopify. La lógica vive en
    `orders/functions/`."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request):
        serializer = OrderListQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data
        filters = {
            "marketplace": params.get("marketplace"),
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "search": params["search"],
        }

        paginator = OrderPagination()
        page = paginator.paginate_queryset(filter_orders(**filters), request, view=self)
        last_synced_at = (
            ShopifyOrderSyncState.objects.filter(pk=1).values_list("last_reconciled_at", flat=True).first()
        )
        return Response(
            {
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "orders": format_orders(page),
                **list_orders_not_created(**filters),
                "last_synced_at": timezone.localtime(last_synced_at).isoformat() if last_synced_at else None,
            }
        )
