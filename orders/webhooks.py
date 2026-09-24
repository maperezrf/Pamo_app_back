"""Webhooks entrantes de proveedores externos. Sigue
docs/patterns/PROVIDER_WEBHOOKS.md: `AllowAny`, trabajo real despachado a
segundo plano vía `orchestrator`, respuesta inmediata."""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from config.constants import MERCADOLIBRE_CLIENT_ID
from integrations.mercadolibre.functions.get_connected_seller_id import get_connected_seller_id
from orchestrator.services import launch_process

from .functions.parse_mercadolibre_notification import parse_mercadolibre_notification


class MercadoLibreOrderWebhookView(APIView):
    """Recibe las notificaciones `orders_v2` de Mercado Libre.

    Mercado Libre no firma sus notificaciones: en vez de verificar firma,
    se valida tópico, app y cuenta, y el pedido se lee después de la API
    con el token propio (el payload nunca se usa como dato). Una
    notificación inválida también responde `200`, para no provocar
    reintentos ni dar pistas.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        order_id = parse_mercadolibre_notification(
            request.data, application_id=MERCADOLIBRE_CLIENT_ID, seller_id=get_connected_seller_id()
        )
        if order_id:
            launch_process(code="orders.process_mercadolibre_notification", params={"order_id": order_id})
        return Response(status=200)
