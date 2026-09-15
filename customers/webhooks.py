"""Webhooks entrantes de proveedores externos. Sigue
docs/patterns/PROVIDER_WEBHOOKS.md: `AllowAny` + firma verificada sobre el
body crudo, trabajo real despachado a segundo plano vía `orchestrator`,
respuesta inmediata -- nunca procesar nada pesado dentro del request."""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.shopify.client import ShopifyClient
from orchestrator.services import launch_process


class ShopifyCustomerWebhookView(APIView):
    """Recibe los eventos `customers/create`/`customers/update` de Shopify."""

    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not ShopifyClient.verify_webhook_signature(request.body, signature):
            return Response(status=403)

        topic = request.headers.get("X-Shopify-Topic", "")
        launch_process(
            code="customers.process_webhook",
            params={"topic": topic, "payload": request.data},
        )
        return Response(status=200)
