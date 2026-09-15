import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from config.constants import FALABELLA_API_KEY, FALABELLA_USER_ID

from .constants import BASE_URL

USER_AGENT = "Pamo-App-Backend/1.0"


class FalabellaAPIError(Exception):
    """La API respondió, pero con un error de negocio (credenciales
    inválidas, parámetro rechazado, etc.) -- no es un fallo de red."""


class FalabellaClient:
    """Conexión con la Seller Center API de Falabella. Solo lo necesario
    para conectarse y hacer una solicitud: firmar y enviar. No sabe qué
    acciones existen ni qué parámetros necesita cada una -- eso vive en
    integrations/falabella/functions/, igual que ShopifyClient no sabe
    qué es un pedido y solo sabe mandar un documento GraphQL."""

    def __init__(self):
        self._user_id = FALABELLA_USER_ID
        self._api_key = FALABELLA_API_KEY

    def _canonical_query(self, parameters):
        return "&".join(
            f"{quote(str(name), safe='~-._')}={quote(str(value), safe='~-._')}"
            for name, value in sorted(parameters.items())
            if value is not None
        )

    def _sign(self, parameters):
        canonical = self._canonical_query(parameters)
        signature = hmac.new(
            self._api_key.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        return f"{canonical}&Signature={quote(signature, safe='~-._')}"

    def request(self, action, version, parameters):
        """Firma y envía una acción de la Seller Center API. `parameters`
        son los campos propios de esa acción (sin Action/Format/Timestamp/
        UserID/Version/Signature -- esos los agrega este método)."""
        signed_parameters = {
            **parameters,
            "Action": action,
            "Format": "JSON",
            "Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "UserID": self._user_id,
            "Version": version,
        }
        response = requests.get(
            f"{BASE_URL}?{self._sign(signed_parameters)}",
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        # NOTA: no se conoce todavía la forma exacta de un error de negocio
        # de esta cuenta (ej. envoltorio "ErrorResponse"). Confirmar y
        # ajustar este chequeo con la primera respuesta real.
        if isinstance(payload, dict) and "ErrorResponse" in payload:
            raise FalabellaAPIError(payload["ErrorResponse"])
        return payload
