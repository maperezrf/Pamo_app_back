"""Webhooks entrantes de proveedores externos. Sigue
docs/patterns/PROVIDER_WEBHOOKS.md: `AllowAny`, trabajo real despachado a
segundo plano vía `orchestrator`, respuesta inmediata."""

import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from config.constants import MERCADOLIBRE_CLIENT_ID
from integrations.mercadolibre.functions.get_connected_seller_id import get_connected_seller_id
from integrations.shopify.client import ShopifyClient
from orchestrator.services import launch_process

from .functions.parse_mercadolibre_notification import parse_mercadolibre_notification

logger = logging.getLogger(__name__)

# Tope del body registrado por el webhook de captura de Madecentro: un
# envío grande o malicioso no debe llenar los logs.
MADECENTRO_CAPTURE_MAX_BYTES = 20 * 1024


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


class MadecentroOrderWebhookView(APIView):
    """TEMPORAL (fase 0): captura los webhooks de pedidos de Madecentro
    (Shipturtle, order create/update) para conocer su forma real.

    Solo registra headers y body en los logs y responde `200`: no valida,
    no persiste y no lanza procesos. Se lee `request.body` (bytes crudos),
    no `request.data`, porque el cuerpo puede no ser JSON y porque una
    firma futura se calcularía sobre esos bytes. Nivel `warning` porque el
    proyecto no configura `LOGGING` y `info` no aparecería en Railway.
    Ver docs/apps/orders.md.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        body = request.body
        truncated = len(body) > MADECENTRO_CAPTURE_MAX_BYTES
        logger.warning(
            "Madecentro webhook capturado: content_type=%s headers=%s body_bytes=%s truncated=%s body=%s",
            request.content_type,
            dict(request.headers),
            len(body),
            truncated,
            body[:MADECENTRO_CAPTURE_MAX_BYTES].decode("utf-8", errors="replace"),
        )
        return Response(status=200)


# Temas de Shopify que mantienen la copia local de pedidos.
SHOPIFY_ORDER_TOPICS = {"orders/create", "orders/updated", "orders/delete"}


class ShopifyOrderWebhookView(APIView):
    """Recibe `orders/create`, `orders/updated` y `orders/delete` de Shopify
    para la copia local de pedidos (ver
    docs/apps/orders.md).

    La firma (`X-Shopify-Hmac-Sha256`) se verifica sobre el body crudo antes
    de leer `request.data`; sin firma válida, `403`. Del aviso solo se toma
    el tema y el id del pedido: el proceso vuelve a leer el pedido de
    Shopify. Un tema desconocido o un aviso sin id responde `200` sin lanzar
    nada, para que Shopify no reintente.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not ShopifyClient.verify_webhook_signature(request.body, signature):
            return Response(status=403)

        topic = request.headers.get("X-Shopify-Topic", "")
        order_id = request.data.get("id") if isinstance(request.data, dict) else None
        if topic in SHOPIFY_ORDER_TOPICS and order_id:
            launch_process(
                code="orders.process_shopify_order_webhook",
                params={"topic": topic, "order_id": str(order_id)},
            )
        return Response(status=200)
