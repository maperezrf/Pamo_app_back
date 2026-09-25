import requests

from config.constants import MADECENTRO_API_BASE_URL


class MadecentroAPIError(Exception):
    """La API de Shipturtle (Madecentro) respondió con un error de
    transporte o con un cuerpo que no es JSON. El mensaje es un código;
    nunca incluye el token ni el cuerpo de la respuesta."""


class MadecentroClient:
    """Conexión con la API de Shipturtle de Madecentro. Solo autentica y
    envía la solicitud -- no sabe qué es un pedido, eso vive en
    integrations/madecentro/functions/.

    Recibe el token porque Shipturtle entrega uno por dominio (pedidos,
    productos): cada función pasa el que le corresponde.
    """

    def __init__(self, token):
        if not token:
            raise MadecentroAPIError("MADECENTRO_TOKEN_MISSING")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def get(self, path, params=None, timeout=(5, 30)):
        response = requests.get(
            f"{MADECENTRO_API_BASE_URL}{path}",
            params=params,
            headers=self._headers,
            timeout=timeout,
            allow_redirects=False,
        )
        if response.is_redirect:
            raise MadecentroAPIError("MADECENTRO_REDIRECT_BLOCKED")
        if not 200 <= response.status_code < 300:
            raise MadecentroAPIError(f"MADECENTRO_HTTP_{response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise MadecentroAPIError("MADECENTRO_RESPONSE_INVALID") from error
