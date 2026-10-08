from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from integrations.envia.client import EnviaAPIError
from integrations.envia.functions.create_label import LabelUnknownResult, LabelWritesDisabled
from integrations.envia_fulfillment.client import EnviaFulfillmentAPIError

from .functions.dispatch_actions import (
    DispatchActionError,
    fetch_channel_label_now,
    generate_dispatch_label,
    notify_dispatch_now,
    quote_dispatch_label,
)
from .functions.list_orders import filter_orders, format_dispatch, format_orders
from .functions.list_orders_not_created import list_orders_not_created
from .models import ShopifyOrder, ShopifyOrderSyncState
from .serializers import OrderListQuerySerializer

# Supuesto de diseño (docs/apps/orders.md, "Listado"): los mismos roles del
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


class DispatchActionAPI(RoleRequiredMixin, APIView):
    """Acciones manuales sobre el despacho de UN pedido de Shopify, desde el
    panel (botones "Traer/Generar guía" y "Notificar a proveedor"). Para
    probar el flujo de forma controlada mientras no se automatiza. La lógica
    vive en `orders/functions/dispatch_actions.py`; las escrituras en Envía
    siguen sujetas a `ENVIA_WRITES_ENABLED` / `ENVIA_FULFILLMENT_WRITES_ENABLED`.
    Respuesta: {"dispatch": ...} (misma forma que en `GET /api/orders/`) o
    {"detail": ...} con 400 (no aplica), 409 (escritura apagada) o 502
    (proveedor; "resultado incierto" incluido)."""

    allowed_roles = ORDERS_LIST_ROLES

    def run(self, order, request):
        raise NotImplementedError

    def post(self, request, shopify_order_id):
        order = get_object_or_404(ShopifyOrder, shopify_id=str(shopify_order_id), deleted_at__isnull=True)
        try:
            return self.run(order, request)
        except DispatchActionError as error:
            return Response({"detail": str(error)}, status=400)
        except ValueError as error:  # validación del payload de Envía, antes de enviar
            return Response({"detail": str(error)}, status=400)
        except LabelWritesDisabled:
            return Response({"detail": "Generación de guías apagada (ENVIA_WRITES_ENABLED)."}, status=409)
        except LabelUnknownResult as error:
            return Response({"detail": f"Resultado incierto: {error}. Revisar en Envía antes de reintentar."}, status=502)
        except (EnviaAPIError, EnviaFulfillmentAPIError) as error:
            return Response({"detail": f"Envía respondió con error: {error}"}, status=502)

    @staticmethod
    def dispatch_response(order):
        order.refresh_from_db()
        return Response({"dispatch": format_dispatch(order)})


class DispatchNotifyAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/notify/: avisa a la bodega por sus
    canales (trae la guía si hace falta)."""

    def run(self, order, request):
        notify_dispatch_now(order)
        return self.dispatch_response(order)


class DispatchFetchLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/fetch/: trae la guía del canal
    (Mercado Libre, Falabella)."""

    def run(self, order, request):
        fetch_channel_label_now(order)
        return self.dispatch_response(order)


class DispatchQuoteLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/quote/: opciones de guía de Envía
    (no cobra). Responde {"options": [{"carrier", "carrierLabel",
    "service", "price", "currency", "etaMinDays", "etaMaxDays"}]}."""

    def run(self, order, request):
        keys = ("carrier", "carrierLabel", "service", "price", "currency", "etaMinDays", "etaMaxDays")
        options = [{key: option.get(key) for key in keys} for option in quote_dispatch_label(order)]
        return Response({"options": options})


class DispatchGenerateLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/ con {"carrier", "service"}:
    genera la guía en Envía (**cobra**). Nunca genera una segunda."""

    def run(self, order, request):
        carrier = str(request.data.get("carrier") or "").strip()
        service = str(request.data.get("service") or "").strip()
        if not carrier or not service:
            return Response({"detail": "Falta la opción elegida (carrier y service)."}, status=400)
        generate_dispatch_label(order, carrier=carrier, service=service)
        return self.dispatch_response(order)
