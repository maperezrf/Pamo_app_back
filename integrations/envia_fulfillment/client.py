import json
import re

import requests

from config.constants import (
    ENVIA_FULFILLMENT_API_TOKEN,
    ENVIA_FULFILLMENT_BASE_URL,
    ENVIA_FULFILLMENT_COMPANY_ID,
    ENVIA_FULFILLMENT_WRITES_ENABLED,
)

# Una guía en base64 dentro de la orden ocupa ~1,4x el PDF.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEZONE = "America/Bogota"
READ_TIMEOUT = (5, 20)
WRITE_TIMEOUT = (5, 60)


class EnviaFulfillmentAPIError(Exception):
    """Envía Fulfillment respondió con un error (HTTP o dentro del cuerpo),
    o con algo que no es JSON. `provider_message` trae el texto del
    proveedor cuando lo hay; nunca el token."""

    def __init__(self, code, *, status_code=None, provider_message=""):
        self.code = code
        self.status_code = status_code
        self.provider_message = provider_message
        super().__init__(f"{code}: {provider_message}" if provider_message else code)


class EnviaFulfillmentWritesDisabled(EnviaFulfillmentAPIError):
    """Escritura bloqueada por configuración (`ENVIA_FULFILLMENT_WRITES_ENABLED`)."""

    def __init__(self, method, path):
        super().__init__("ENVIA_FULFILLMENT_WRITES_DISABLED", provider_message=f"{method} {path}")


class EnviaFulfillmentClient:
    """Conexión con la API de Envía Fulfillment (`apifulfillment.envia.com`),
    distinta de la de Shipping (`integrations/envia/`). Solo autentica y
    envía; no sabe qué es una orden. `{company_id}` en `path` se reemplaza
    por `ENVIA_FULFILLMENT_COMPANY_ID`.

    Toda solicitud que no sea GET es una escritura y se bloquea mientras
    `ENVIA_FULFILLMENT_WRITES_ENABLED` sea falso, antes de tocar la red.
    """

    def __init__(self):
        if not ENVIA_FULFILLMENT_API_TOKEN:
            raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_TOKEN_MISSING")
        self.base_url = ENVIA_FULFILLMENT_BASE_URL.rstrip("/")

    def get(self, path, params=None):
        return self._send("GET", path, params=params)

    def post(self, path, payload):
        return self._send("POST", path, payload=payload)

    def put(self, path, payload):
        return self._send("PUT", path, payload=payload)

    def _send(self, method, path, params=None, payload=None):
        path = self._resolve(path)
        if method != "GET" and not ENVIA_FULFILLMENT_WRITES_ENABLED:
            raise EnviaFulfillmentWritesDisabled(method, path)
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            # UTF-8 real: la API de Shipping de Envía rechaza texto Unicode
            # escapado (ver integrations/envia/client.py); se asume igual.
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {ENVIA_FULFILLMENT_API_TOKEN}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                # El listado de órdenes responde 400 sin este header (no
                # basta como parámetro); la colección oficial lo manda así.
                "timezone": TIMEZONE,
            },
            timeout=READ_TIMEOUT if method == "GET" else WRITE_TIMEOUT,
            allow_redirects=False,
        )
        if response.is_redirect:
            raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_REDIRECT_BLOCKED", status_code=response.status_code)
        return _parse(response)

    @staticmethod
    def _resolve(path):
        if "{company_id}" in path:
            if not ENVIA_FULFILLMENT_COMPANY_ID:
                raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_COMPANY_ID_MISSING")
            path = path.replace("{company_id}", str(ENVIA_FULFILLMENT_COMPANY_ID))
        if not path.startswith("/") or re.search(r"[{}]", path):
            raise ValueError(f"Ruta inválida: {path}")
        return path


def _parse(response):
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_RESPONSE_TOO_LARGE", status_code=response.status_code)
    try:
        payload = json.loads(response.content.decode("utf-8")) if response.content else None
    except (UnicodeError, ValueError):
        raise EnviaFulfillmentAPIError("ENVIA_FULFILLMENT_RESPONSE_NOT_JSON", status_code=response.status_code) from None
    if not 200 <= response.status_code < 300:
        raise EnviaFulfillmentAPIError(
            f"ENVIA_FULFILLMENT_HTTP_{response.status_code}",
            status_code=response.status_code,
            provider_message=_message(payload),
        )
    return payload


def _message(payload):
    if isinstance(payload, dict):
        for key in ("message", "error", "errors", "detail"):
            if payload.get(key):
                return str(payload[key])[:500]
    return ""
