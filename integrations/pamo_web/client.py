import requests

from config.constants import PAMO_WEB_BASE_URL, PAMO_WEB_BOT_TOKEN

BOT_TOKEN_HEADER = "X-Bot-Token"


class PamoWebAPIError(Exception):
    """pamo_web no está configurado, respondió con un error HTTP o con un
    cuerpo que no es JSON. El mensaje es un código; nunca incluye el
    token."""


class PamoWebClient:
    """Conexión temporal con pamo_web (el sistema anterior, en Railway),
    mientras termina de facturar sus OC de Sodimac antiguas. Solo autentica
    (header X-Bot-Token, el BOT_API_TOKEN de pamo_web) y envía la
    solicitud -- qué endpoint se llama vive en
    integrations/pamo_web/functions/. Se quita al terminar la convivencia
    (docs/implementations-plans/sodimac-orders-and-invoicing.md, Parte C).
    """

    def __init__(self):
        if not PAMO_WEB_BASE_URL or not PAMO_WEB_BOT_TOKEN:
            raise PamoWebAPIError("PAMO_WEB_NOT_CONFIGURED")
        self._headers = {BOT_TOKEN_HEADER: PAMO_WEB_BOT_TOKEN, "Accept": "application/json"}

    def post(self, path, timeout):
        response = requests.post(
            f"{PAMO_WEB_BASE_URL.rstrip('/')}{path}",
            headers=self._headers,
            timeout=timeout,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise PamoWebAPIError(f"PAMO_WEB_HTTP_{response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise PamoWebAPIError("PAMO_WEB_RESPONSE_INVALID") from error
