import base64
import hashlib
import hmac

import requests

from config.constants import SHOPIFY_ACCESS_TOKEN, SHOPIFY_GRAPHQL_URL, SHOPIFY_WEBHOOK_SECRET


class ShopifyGraphQLError(Exception):
    """Shopify devolvió `errors` a nivel superior del documento GraphQL
    (permisos insuficientes, límite de tasa, documento inválido, etc.) --
    distinto de un `userErrors` de negocio dentro de una mutation."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


class ShopifyClient:
    """Transporte GraphQL hacia Shopify. Por ahora solo se usa esta API
    (no REST) -- ver integrations/shopify/queries.py para el catálogo
    de queries/mutations disponibles."""

    def __init__(self):
        self._headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json",
        }

    def request_graphql(self, query, variables=None):
        response = requests.post(
            SHOPIFY_GRAPHQL_URL,
            headers=self._headers,
            json={"query": query, "variables": variables or {}},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise ShopifyGraphQLError(payload["errors"])
        return payload

    @staticmethod
    def verify_webhook_signature(raw_body, header_signature):
        """Verifica la firma de un webhook entrante de Shopify (header
        `X-Shopify-Hmac-Sha256`). Shopify firma con HMAC-SHA256 + base64
        (no hex, a diferencia de WhatsApp/Meta -- ver
        `integrations/whatsapp/client.py::verify_signature`).

        `raw_body` debe ser el cuerpo crudo del request (`request.body`,
        bytes), nunca `request.data` ya parseado -- la firma se calculó
        sobre los bytes exactos que mandó Shopify, no sobre una
        re-serialización. Ver docs/patterns/PROVIDER_WEBHOOKS.md.
        """
        if not header_signature:
            return False
        expected = base64.b64encode(
            hmac.new(SHOPIFY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, header_signature)
