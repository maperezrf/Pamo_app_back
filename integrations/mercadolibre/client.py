from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from django.db import transaction

from config.constants import (
    MERCADOLIBRE_API_BASE_URL,
    MERCADOLIBRE_AUTH_URL,
    MERCADOLIBRE_CLIENT_ID,
    MERCADOLIBRE_CLIENT_SECRET,
    MERCADOLIBRE_REDIRECT_URI,
)

from .models import MercadoLibreToken

SINGLETON_ID = 1
TIMEOUT = 30
# Se renueva un poco antes de que venza, para no mandar un token que
# expire en pleno request.
REFRESH_MARGIN = timedelta(minutes=5)
# Mercado Libre documenta 6 h; se usa solo si la respuesta no trae
# `expires_in`.
DEFAULT_EXPIRES_IN = 21600
REAUTHORIZE_HINT = "volver a conectar la cuenta en GET /api/integrations/mercadolibre/connect/"


class MercadoLibreAPIError(Exception):
    """Mercado Libre respondió con un error HTTP -- incluye el cuerpo de
    la respuesta cuando viene (ahí suele estar `error`/`message`/`cause`)."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class MercadoLibreAuthError(Exception):
    """No hay una conexión utilizable con Mercado Libre: faltan
    credenciales, nunca se autorizó la cuenta, o el refresh token ya no es
    válido. Se resuelve re-autorizando, no reintentando."""


class MercadoLibreClient:
    """Conexión con la API de Mercado Libre (OAuth2). Solo autenticación y
    transporte -- no sabe qué es un pedido o un envío, eso vive en
    integrations/mercadolibre/functions/.

    El token vive en MercadoLibreToken (fila única). El refresh token es
    de un solo uso y rota en cada renovación: si dos procesos renuevan a la
    vez con el mismo refresh token, el segundo falla y la conexión queda
    inválida. Por eso la renovación bloquea la fila (`select_for_update`),
    relee el estado tras el bloqueo y guarda el refresh token nuevo antes
    de devolver el access token.
    """

    @staticmethod
    def authorization_url(state):
        """URL de autorización de Mercado Libre; al aceptar, redirige a
        MERCADOLIBRE_REDIRECT_URI con `?code=...&state=...`. `state` lo
        genera y valida quien inicia el flujo (anti-CSRF), ver
        integrations/mercadolibre/apis.py."""
        _require_credentials()
        if not MERCADOLIBRE_REDIRECT_URI:
            raise MercadoLibreAuthError("MERCADOLIBRE_REDIRECT_URI no está configurado")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": MERCADOLIBRE_CLIENT_ID,
                "redirect_uri": MERCADOLIBRE_REDIRECT_URI,
                "state": state,
            }
        )
        return f"{MERCADOLIBRE_AUTH_URL}?{query}"

    def exchange_code(self, code):
        """Intercambia el `code` de autorización por el primer token y lo
        guarda (crea o reemplaza la fila). Devuelve el seller id."""
        if not MERCADOLIBRE_REDIRECT_URI:
            raise MercadoLibreAuthError("MERCADOLIBRE_REDIRECT_URI no está configurado")
        data = _token_request(
            {
                "grant_type": "authorization_code",
                "client_id": MERCADOLIBRE_CLIENT_ID,
                "client_secret": MERCADOLIBRE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": MERCADOLIBRE_REDIRECT_URI,
            }
        )
        token = MercadoLibreToken(id=SINGLETON_ID)
        _apply_token_response(token, data)
        token.save()
        return token.user_id

    def get(self, path, params=None, headers=None):
        """GET autenticado. `path` relativo a MERCADOLIBRE_API_BASE_URL,
        con `/` inicial (ej. "/orders/123"). Ante un 401 renueva el token y
        reintenta una sola vez."""
        access_token = self._valid_access_token()
        response = self._send(path, params, headers, access_token)
        if response.status_code == 401:
            access_token = self._refresh(stale_access_token=access_token)
            response = self._send(path, params, headers, access_token)
        if not response.ok:
            raise MercadoLibreAPIError(response.status_code, _body(response))
        return response.json()

    def _send(self, path, params, headers, access_token):
        return requests.get(
            f"{MERCADOLIBRE_API_BASE_URL}{path}",
            params=params,
            headers={"Accept": "application/json", **(headers or {}), "Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )

    def _valid_access_token(self):
        token = MercadoLibreToken.objects.filter(id=SINGLETON_ID).first()
        if token is None:
            raise MercadoLibreAuthError(f"Mercado Libre no está autorizado: {REAUTHORIZE_HINT}")
        if token.expires_at - REFRESH_MARGIN > datetime.now(timezone.utc):
            return token.access_token
        return self._refresh(stale_access_token=token.access_token)

    def _refresh(self, stale_access_token):
        """Renueva el token salvo que otro proceso ya lo haya hecho
        mientras se esperaba el bloqueo (el access token guardado ya no es
        el que se vio vencido/rechazado y sigue vigente)."""
        with transaction.atomic():
            token = MercadoLibreToken.objects.select_for_update().filter(id=SINGLETON_ID).first()
            if token is None:
                raise MercadoLibreAuthError(f"Mercado Libre no está autorizado: {REAUTHORIZE_HINT}")
            still_valid = token.expires_at - REFRESH_MARGIN > datetime.now(timezone.utc)
            if token.access_token != stale_access_token and still_valid:
                return token.access_token
            data = _token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": MERCADOLIBRE_CLIENT_ID,
                    "client_secret": MERCADOLIBRE_CLIENT_SECRET,
                    "refresh_token": token.refresh_token,
                }
            )
            _apply_token_response(token, data)
            token.save()
            return token.access_token


def _require_credentials():
    if not MERCADOLIBRE_CLIENT_ID or not MERCADOLIBRE_CLIENT_SECRET:
        raise MercadoLibreAuthError("MERCADOLIBRE_CLIENT_ID / MERCADOLIBRE_CLIENT_SECRET no están configurados")


def _token_request(data):
    _require_credentials()
    response = requests.post(
        f"{MERCADOLIBRE_API_BASE_URL}/oauth/token",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    if not response.ok:
        body = _body(response)
        if isinstance(body, dict) and body.get("error") == "invalid_grant":
            raise MercadoLibreAuthError(f"Mercado Libre rechazó el código/refresh token (invalid_grant): {REAUTHORIZE_HINT}")
        raise MercadoLibreAPIError(response.status_code, body)
    return response.json()


def _apply_token_response(token, data):
    token.access_token = data["access_token"]
    # Mercado Libre manda un refresh token nuevo en cada renovación; el
    # anterior deja de servir.
    token.refresh_token = data["refresh_token"]
    token.user_id = str(data["user_id"])
    token.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in") or DEFAULT_EXPIRES_IN))


def _body(response):
    try:
        return response.json()
    except ValueError:
        return response.text
