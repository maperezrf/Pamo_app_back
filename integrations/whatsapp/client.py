import hashlib
import hmac

import requests

from config.constants import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)

GRAPH_BASE_URL = "https://graph.facebook.com"
TIMEOUT = (5, 20)


class WhatsAppAPIError(Exception):
    """Meta respondió con un error HTTP -- incluye el cuerpo de la
    respuesta cuando Meta lo manda (ahí suele venir el código de error
    de negocio, ej. plantilla no aprobada o ventana de servicio vencida)."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class WhatsAppClient:
    """Conexión con la API de WhatsApp Cloud (Meta). Solo autentica y
    transporta -- no sabe qué es una plantilla, un pedido o un botón,
    eso vive en integrations/whatsapp/functions/."""

    def __init__(self):
        self.base_url = f"{GRAPH_BASE_URL}/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}"

    def send_message(self, payload):
        response = requests.post(
            f"{self.base_url}/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    def upload_media(self, content, filename, mime_type):
        response = requests.post(
            f"{self.base_url}/media",
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename, content, mime_type)},
            headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    @staticmethod
    def _parse(response):
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if not response.ok:
            raise WhatsAppAPIError(response.status_code, body)
        return body

    @staticmethod
    def verify_signature(raw_body, header_signature):
        if not header_signature or not header_signature.startswith("sha256="):
            return False
        expected = hmac.new(WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        received = header_signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, received)
