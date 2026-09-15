import requests
from rest_framework.exceptions import APIException

from config.constants import SIIGO_ACCESS_KEY, SIIGO_API_URL, SIIGO_PARTNER_ID, SIIGO_USERNAME


class SiigoAPIError(APIException):
    status_code = 502
    default_detail = "Siigo no respondió correctamente."
    default_code = "siigo_api_error"


class SiigoClient:
    """Cliente HTTP a la API de Siigo. Los errores de red (timeout, conexión)
    se dejan propagar tal cual -- el llamador decide si el resultado quedó
    en un estado desconocido; los errores de respuesta HTTP se traducen a
    SiigoAPIError."""

    def _headers(self, token=None):
        headers = {"Content-Type": "application/json", "Partner-Id": SIIGO_PARTNER_ID}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def authenticate(self):
        response = requests.post(
            f"{SIIGO_API_URL}/auth",
            json={"username": SIIGO_USERNAME, "access_key": SIIGO_ACCESS_KEY},
            headers=self._headers(),
            timeout=15,
        )
        if response.status_code != 200:
            raise SiigoAPIError(f"No se pudo autenticar con Siigo ({response.status_code}).")
        token = response.json().get("access_token")
        if not token:
            raise SiigoAPIError("Siigo no devolvió un access_token.")
        return token

    def create_invoice(self, payload):
        token = self.authenticate()
        response = requests.post(
            f"{SIIGO_API_URL}/v1/invoices",
            json=payload,
            headers=self._headers(token),
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise SiigoAPIError(f"Siigo rechazó la factura ({response.status_code}): {response.text[:500]}")
        return response.json()
