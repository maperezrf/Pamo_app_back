from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import BearerTokenRequiredMixin, RoleRequiredMixin
from config.constants import SODIMAC_RPA_TOKEN

from integrations.envia.client import EnviaAPIError
from integrations.envia.functions.create_label import LabelUnknownResult
from integrations.envia_fulfillment.client import EnviaFulfillmentAPIError
from integrations.sodimac_rpa.client import SodimacRpaBusy, SodimacRpaError

from .functions.bulk_notify import BulkNotifyError, bulk_notify_preview, notify_location
from .functions.channel_label_file import ChannelLabelFileNotFound, channel_label_file
from .functions.dispatch_actions import (
    DispatchActionError,
    DispatchNotFound,
    dispatch_for_action,
    envia_label_file,
    fetch_channel_label_now,
    generate_dispatch_label,
    notify_dispatch_now,
    parse_packages,
    quote_dispatch_label,
    release_envia_label,
    upload_dispatch_label,
)
from .functions.label_links import LabelLinkExpired, LabelLinkInvalid, label_from_link
from .functions.list_orders import channel_label_for_order, filter_orders, format_dispatch, format_dispatches, format_orders
from .functions.list_orders_not_created import list_orders_not_created
from .functions.sodimac_rpa import (
    LabelRequestError,
    LabelStoreError,
    RpaControlError,
    RpaEventError,
    bulk_label_preview,
    process_rpa_event,
    request_bulk_labels,
    request_label_for_order,
    rpa_continue,
    rpa_kill,
    rpa_panel_status,
    stock_dispatch,
    upload_label_for_order,
)
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


def _order_dispatch(shopify_order_id, dispatch_id, *, create=True):
    """(pedido, despacho) de un botón del panel; 404 si el pedido no existe o
    el despacho no es suyo. Un pedido repartido en varias bodegas exige
    `dispatch_id` (`DispatchActionError` → 400)."""
    order = get_object_or_404(ShopifyOrder, shopify_id=str(shopify_order_id), deleted_at__isnull=True)
    try:
        return order, dispatch_for_action(order, dispatch_id, create=create)
    except DispatchNotFound as error:
        raise Http404(str(error)) from error


class DispatchActionAPI(RoleRequiredMixin, APIView):
    """Acciones manuales sobre UN despacho de un pedido de Shopify, desde el
    panel (botones "Traer/Generar guía" y "Notificar a proveedor"). Para
    probar el flujo de forma controlada mientras no se automatiza. La lógica
    vive en `orders/functions/dispatch_actions.py`.
    Cuerpo: `dispatch_id` opcional; obligatorio si el pedido sale de varias
    bodegas (un despacho por bodega).
    Respuesta: {"dispatch": ..., "dispatches": [...]} (el despacho de la
    acción y todos los del pedido, misma forma que en `GET /api/orders/`) o
    {"detail": ...} con 400 (no aplica) o 502 (proveedor; "resultado
    incierto" incluido)."""

    allowed_roles = ORDERS_LIST_ROLES

    def run(self, dispatch, request):
        raise NotImplementedError

    def post(self, request, shopify_order_id):
        try:
            _, dispatch = _order_dispatch(shopify_order_id, request.data.get("dispatch_id"))
            return self.run(dispatch, request)
        except DispatchActionError as error:
            return Response({"detail": str(error)}, status=400)
        except ValueError as error:  # validación del payload de Envía, antes de enviar
            return Response({"detail": str(error)}, status=400)
        except LabelUnknownResult as error:
            return Response({"detail": f"Resultado incierto: {error}. Revisar en Envía antes de reintentar."}, status=502)
        except (EnviaAPIError, EnviaFulfillmentAPIError) as error:
            return Response({"detail": f"Envía respondió con error: {error}"}, status=502)

    @staticmethod
    def dispatch_response(dispatch):
        dispatch.refresh_from_db()
        return Response({"dispatch": format_dispatch(dispatch), "dispatches": format_dispatches(dispatch.shopify_order)})


class DispatchNotifyAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/notify/: avisa a la bodega por sus
    canales (trae la guía si hace falta)."""

    def run(self, dispatch, request):
        notify_dispatch_now(dispatch)
        return self.dispatch_response(dispatch)


class DispatchBulkNotifyAPI(RoleRequiredMixin, APIView):
    """GET/POST /api/orders/dispatch/bulk-notify/: botón "Notificar
    bodegas". GET: pedidos que esperan aviso agrupados por bodega
    (`bulk_notify_preview`). POST `{"location_id"}`: avisa en un solo
    mensaje por canal los pedidos listos de esa bodega; 200 `{"location",
    "notified", "failed", "waiting"}`; 400 bodega desconocida o sin
    pedidos listos."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request):
        return Response(bulk_notify_preview())

    def post(self, request):
        try:
            return Response(notify_location(request.data.get("location_id")))
        except BulkNotifyError as error:
            return Response({"detail": str(error)}, status=400)


class DispatchFetchLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/fetch/: trae la guía del canal
    (Mercado Libre, Falabella)."""

    def run(self, dispatch, request):
        fetch_channel_label_now(dispatch)
        return self.dispatch_response(dispatch)


class DispatchQuoteLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/quote/: opciones de guía de Envía
    (no cobra). Responde {"options": [{"carrier", "carrierLabel",
    "service", "price", "currency", "etaMinDays", "etaMaxDays"}], "route":
    {"origin": {"name", "city"}, "destination": {"name", "address", "city",
    "region", "phone"}}, "packages": [{"weight", "length", "width", "height",
    "quantity"}]}. Cuerpo opcional: `packages`, las cajas con peso (kg),
    medidas (cm) y cantidad, todas en una sola guía (`package`, una sola
    caja, sigue sirviendo)."""

    def run(self, dispatch, request):
        keys = ("carrier", "carrierLabel", "service", "price", "currency", "etaMinDays", "etaMaxDays")
        packages = parse_packages(request.data.get("packages") or request.data.get("package"))
        options, route = quote_dispatch_label(dispatch, packages)
        return Response({
            "options": [{key: option.get(key) for key in keys} for option in options],
            "route": route,
            "packages": packages,
        })


class DispatchGenerateLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/ con {"carrier", "service"} y
    el `package` cotizado: genera la guía en Envía (**cobra**). Nunca genera
    una segunda."""

    def run(self, dispatch, request):
        carrier = str(request.data.get("carrier") or "").strip()
        service = str(request.data.get("service") or "").strip()
        if not carrier or not service:
            return Response({"detail": "Falta la opción elegida (carrier y service)."}, status=400)
        packages = parse_packages(request.data.get("packages") or request.data.get("package"))
        generate_dispatch_label(dispatch, carrier=carrier, service=service, packages=packages)
        return self.dispatch_response(dispatch)


class DispatchUploadLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/upload/ (multipart): botón "Cargar
    guía" de los pedidos que no traen guía del canal. Campos `label` (PDF),
    `tracking_number`, `carrier` y `dispatch_id` opcional. 400 si ya tiene
    guía (liberarla primero) o los datos no sirven."""

    parser_classes = [MultiPartParser]

    def run(self, dispatch, request):
        label = request.FILES.get("label")
        upload_dispatch_label(
            dispatch,
            label.read() if label else b"",
            tracking_number=request.data.get("tracking_number"),
            carrier=request.data.get("carrier"),
        )
        return self.dispatch_response(dispatch)


class DispatchReleaseLabelAPI(DispatchActionAPI):
    """POST /api/orders/<id>/dispatch/label/release/: quita del despacho la
    guía generada en Envía o cargada a mano para generar o cargar otra. No la cancela en Envía (se
    hace a mano). 400 si ya se avisó a la bodega o está en Shopify."""

    def run(self, dispatch, request):
        release_envia_label(dispatch)
        return self.dispatch_response(dispatch)


class DispatchLabelFileAPI(RoleRequiredMixin, APIView):
    """GET /api/orders/<id>/dispatch/label/file/[?dispatch_id=N]: descarga el
    PDF de la guía generada en Envía (guardada en el despacho). 404 si no
    tiene; `dispatch_id` obligatorio si el pedido sale de varias bodegas."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request, shopify_order_id):
        try:
            _, dispatch = _order_dispatch(shopify_order_id, request.query_params.get("dispatch_id"), create=False)
        except DispatchActionError as error:
            return Response({"detail": str(error)}, status=400)
        try:
            filename, content = envia_label_file(dispatch)
        except DispatchActionError as error:
            return Response({"detail": str(error)}, status=404)
        except EnviaAPIError as error:
            return Response({"detail": f"No se pudo bajar la guía de Envía: {error}"}, status=502)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class DispatchLabelLinkAPI(APIView):
    """GET /api/orders/labels/<token>/: la guía de un despacho por su link
    firmado (`label_links.label_link`), para la bodega, que no tiene sesión.
    Público a propósito: la firma es la autorización (solo esa guía, vence a
    los 10 días). 404 si el link no vale; 410 si venció. Se abre en el
    navegador (inline)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        try:
            filename, content = label_from_link(token)
        except LabelLinkExpired:
            return HttpResponse("El link de la guía venció. Pídela de nuevo.", status=410, content_type="text/plain; charset=utf-8")
        except LabelLinkInvalid:
            return HttpResponse("Link de guía inválido.", status=404, content_type="text/plain; charset=utf-8")
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


class OrderChannelLabelRequestAPI(RoleRequiredMixin, APIView):
    """POST /api/orders/<id>/channel-label/request/: botón "Generar guía
    (RPA)" de los pedidos de Sodimac. Manda la OC al RPA, que genera la guía
    en el portal (real) y la devuelve por webhook. 200 `{"channel_label"}`;
    400 no aplica (ya tiene guía, sin bodega, no es Sodimac); 409 el RPA ya
    está corriendo; 502 el RPA no responde o no está configurado."""

    allowed_roles = ORDERS_LIST_ROLES

    def post(self, request, shopify_order_id):
        order = get_object_or_404(ShopifyOrder, shopify_id=str(shopify_order_id), deleted_at__isnull=True)
        try:
            request_label_for_order(order)
        except LabelRequestError as error:
            return Response({"detail": str(error)}, status=400)
        except SodimacRpaBusy:
            return Response({"detail": "El RPA ya está generando guías; intenta cuando termine."}, status=409)
        except SodimacRpaError as error:
            return Response({"detail": f"No se pudo llamar al RPA: {error}"}, status=502)
        return Response({"channel_label": channel_label_for_order(order)})


class OrderChannelLabelUploadAPI(RoleRequiredMixin, APIView):
    """POST /api/orders/<id>/channel-label/upload/ (multipart): botón "Cargar
    guía" de los pedidos de Sodimac. Campos `label` (PDF del rótulo),
    `relation` (PDF de la relación) y `tracking_number` opcional (si falta,
    se lee "REMESA <n>" del rótulo). Reemplaza la guía anterior mientras la
    bodega no haya sido avisada. 200 `{"channel_label"}`; 400 con el motivo."""

    allowed_roles = ORDERS_LIST_ROLES
    parser_classes = [MultiPartParser]

    def post(self, request, shopify_order_id):
        order = get_object_or_404(ShopifyOrder, shopify_id=str(shopify_order_id), deleted_at__isnull=True)
        label, relation = request.FILES.get("label"), request.FILES.get("relation")
        try:
            upload_label_for_order(
                order,
                label.read() if label else b"",
                relation.read() if relation else b"",
                tracking=request.data.get("tracking_number"),
            )
        except LabelStoreError as error:
            return Response({"detail": str(error)}, status=400)
        return Response({"channel_label": channel_label_for_order(order)})


class OrderChannelLabelFileAPI(RoleRequiredMixin, APIView):
    """GET /api/orders/<id>/channel-label/<rotulo|relacion>/: descarga el PDF
    de la guía que generó un tercero (hoy el RPA de Sodimac). 404 si el
    pedido no tiene guía o el documento no existe."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request, shopify_order_id, kind):
        order = get_object_or_404(ShopifyOrder, shopify_id=str(shopify_order_id), deleted_at__isnull=True)
        try:
            filename, content = channel_label_file(order, kind)
        except ChannelLabelFileNotFound as error:
            return Response({"detail": str(error)}, status=404)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class SodimacRpaStatusAPI(RoleRequiredMixin, APIView):
    """GET /api/orders/sodimac/rpa/status/: recuadro "RPA de guías" del
    panel (estado y enlace del visor). Siempre 200: un RPA caído o sin
    configurar se informa en el cuerpo."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request):
        return Response(rpa_panel_status())


class SodimacRpaControlAPI(RoleRequiredMixin, APIView):
    """POST /api/orders/sodimac/rpa/<continue|kill>/: botones "Continuar"
    (tras resolver el CAPTCHA) y "Detener" del recuadro del RPA. 200 con el
    estado nuevo; 409 si no aplica; 502 si el RPA no responde."""

    allowed_roles = ORDERS_LIST_ROLES
    action = None

    def post(self, request):
        try:
            self.action()
        except RpaControlError as error:
            return Response({"detail": str(error)}, status=409)
        except SodimacRpaError as error:
            return Response({"detail": f"No se pudo llamar al RPA: {error}"}, status=502)
        return Response(rpa_panel_status())


class SodimacRpaBulkLabelsAPI(RoleRequiredMixin, APIView):
    """GET/POST /api/orders/sodimac/rpa/bulk-labels/: botón "Generar guías
    disponibles". GET: vista previa `{"ready", "blocked"}` (OC que esperan
    guía, separadas por si la bodega de la plataforma coincide con la de
    Shopify). POST `{"purchase_orders"}` opcional (las marcadas; sin él,
    todas): la recalcula y manda al RPA solo las `ready` elegidas, en un
    lote; 200 con la vista previa y `sent`; 400 si no hay ninguna; 409 el
    RPA ya está corriendo; 502 el RPA no responde o no está configurado."""

    allowed_roles = ORDERS_LIST_ROLES

    def get(self, request):
        return Response(bulk_label_preview())

    def post(self, request):
        purchase_orders = request.data.get("purchase_orders")
        if purchase_orders is not None and not isinstance(purchase_orders, list):
            return Response({"detail": "purchase_orders debe ser una lista de OC."}, status=400)
        try:
            return Response(request_bulk_labels(purchase_orders))
        except LabelRequestError as error:
            return Response({"detail": str(error)}, status=400)
        except SodimacRpaBusy:
            return Response({"detail": "El RPA ya está generando guías; intenta cuando termine."}, status=409)
        except SodimacRpaError as error:
            return Response({"detail": f"No se pudo llamar al RPA: {error}"}, status=502)


class SodimacRpaStockDispatchAPI(BearerTokenRequiredMixin, APIView):
    """GET /api/orders/sodimac/rpa/orders/<oc>/stock-dispatch/: el RPA de
    guías pregunta desde qué bodega sale la OC para elegir la dirección de
    despacho en el portal. Token `SODIMAC_RPA_TOKEN` (el RPA lo manda como
    `X-Bot-Token`). 200 con `bodega_despacho`, 404 OC desconocida, 409 sin
    bodega (novedad)."""

    bearer_token = SODIMAC_RPA_TOKEN

    def get(self, request, purchase_order):
        body, status = stock_dispatch(purchase_order)
        return Response(body, status=status)


class SodimacRpaCallbackAPI(BearerTokenRequiredMixin, APIView):
    """POST /api/orders/sodimac/rpa/callback/: webhook del RPA de guías
    (`orden_procesada`, `validacion_humana`, `proceso_finalizado`). Token
    `SODIMAC_RPA_TOKEN` en `Authorization: Bearer`. 200 procesado, 400
    payload inválido. La lógica vive en `orders/functions/sodimac_rpa.py`."""

    bearer_token = SODIMAC_RPA_TOKEN
    parser_classes = [JSONParser]

    def post(self, request):
        try:
            result = process_rpa_event(request.data)
        except RpaEventError as error:
            return Response({"detail": str(error)}, status=400)
        return Response({"detail": result})
