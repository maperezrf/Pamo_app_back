import json
import re

import requests

from config.constants import ENVIA_API_TOKEN, ENVIA_ENVIRONMENT

from .constants import QUERIES_BASE, SHIPPING_BASE

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class EnviaAPIError(Exception):
    """Envía respondió, pero con un error -- de transporte (HTTP) o de
    negocio (Envía a veces mete un código 4xx/5xx *dentro* del cuerpo con
    HTTP 200, en vez de usar el código de estado real)."""

    def __init__(self, code, *, provider_error_code=""):
        self.code = code
        self.provider_error_code = provider_error_code
        super().__init__(code)


class EnviaClient:
    """Conexión con la API de Envía. Solo lo necesario para autenticar y
    enviar una solicitud -- no sabe qué es cotizar o generar una guía,
    eso vive en integrations/envia/functions/."""

    def __init__(self):
        if ENVIA_ENVIRONMENT not in SHIPPING_BASE:
            raise EnviaAPIError("ENVIA_ENVIRONMENT_INVALID")
        self.shipping_base = SHIPPING_BASE[ENVIA_ENVIRONMENT]
        self.queries_base = QUERIES_BASE[ENVIA_ENVIRONMENT]

    def _headers(self):
        return {
            "Authorization": f"Bearer {ENVIA_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _parse(self, response):
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise EnviaAPIError("ENVIA_RESPONSE_TOO_LARGE")
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise EnviaAPIError("ENVIA_RESPONSE_INVALID") from error
        if not isinstance(payload, dict):
            raise EnviaAPIError("ENVIA_RESPONSE_INVALID")
        if not 200 <= response.status_code < 300:
            raise EnviaAPIError(
                f"ENVIA_HTTP_{response.status_code}",
                provider_error_code=str(payload.get("code") or payload.get("error") or ""),
            )
        # Envía a veces devuelve HTTP 200 con un código de error de negocio
        # metido en el cuerpo -- verificado en pamo-one-engineering.
        if re.fullmatch(r"[45]\d{2}", str(payload.get("code") or "")):
            raise EnviaAPIError("ENVIA_PROVIDER_ERROR", provider_error_code=str(payload["code"]))
        if str(payload.get("meta") or "").lower() == "error":
            raise EnviaAPIError("ENVIA_PROVIDER_ERROR")
        return payload

    def get(self, base_url, path, params=None, timeout=(3, 8)):
        response = requests.get(
            f"{base_url}{path}",
            params=params,
            headers=self._headers(),
            timeout=timeout,
            allow_redirects=False,
        )
        if response.is_redirect:
            raise EnviaAPIError("ENVIA_REDIRECT_BLOCKED")
        return self._parse(response)

    def post(self, base_url, path, payload, timeout=(5, 35)):
        # Envía rechaza con un 400 confuso algo de texto Unicode escapado
        # -- se preservan nombres/direcciones como bytes UTF-8 reales
        # (verificado en pamo-one-engineering).
        response = requests.post(
            f"{base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            timeout=timeout,
            allow_redirects=False,
        )
        if response.is_redirect:
            raise EnviaAPIError("ENVIA_REDIRECT_BLOCKED")
        return self._parse(response)
