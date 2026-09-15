from datetime import datetime, timedelta, timezone

import requests

from config.constants import (
    SIIGO_ACCESS_KEY,
    SIIGO_API_BASE_URL,
    SIIGO_AUTH_URL,
    SIIGO_PARTNER_ID,
    SIIGO_USERNAME,
)

from .models import SiigoToken

TOKEN_LIFETIME = timedelta(hours=23)
SINGLETON_ID = 1


class SiigoAPIError(Exception):
    """Siigo respondió con un error HTTP -- incluye el cuerpo de la
    respuesta cuando Siigo lo manda (ahí suele venir el detalle de una
    validación rechazada, ej. un id de retención inválido)."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class SiigoClient:
    """Conexión con la API de Siigo. Autentica con un token cacheado en
    BD (SiigoToken) y lo renueva solo cuando está vencido -- no sabe qué
    es un cliente o una factura, eso vive en integrations/siigo/functions/.

    A diferencia del código anterior (que pedía un token nuevo en cada
    instancia porque la validación de expiración estaba comentada), acá
    sí se reutiliza el token mientras siga vigente."""

    def _valid_token(self):
        token = SiigoToken.objects.filter(id=SINGLETON_ID).first()
        if token and token.expires_at > datetime.now(timezone.utc):
            return token.token
        return self._refresh_token()

    def _refresh_token(self):
        response = requests.post(
            SIIGO_AUTH_URL,
            headers={"Content-Type": "application/json", "Partner-Id": SIIGO_PARTNER_ID},
            json={"username": SIIGO_USERNAME, "access_key": SIIGO_ACCESS_KEY},
            timeout=30,
        )
        if not response.ok:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise SiigoAPIError(response.status_code, body)
        value = response.json()["access_token"]
        expires_at = datetime.now(timezone.utc) + TOKEN_LIFETIME
        token, _ = SiigoToken.objects.update_or_create(
            id=SINGLETON_ID, defaults={"token": value, "expires_at": expires_at}
        )
        return token.token

    def request(self, method, path, params=None, json_body=None):
        headers = {
            "Content-Type": "application/json",
            "Partner-Id": SIIGO_PARTNER_ID,
            "Authorization": self._valid_token(),
        }
        response = requests.request(
            method,
            f"{SIIGO_API_BASE_URL}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=30,
        )
        if not response.ok:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise SiigoAPIError(response.status_code, body)
        return response.json()
