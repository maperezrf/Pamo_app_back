import requests

from config.constants import SODIMAC_SUBSCRIPTION_KEY


class SodimacClient:
    """Conexión con la API de Sodimac. Solo lo necesario para autenticar
    y enviar una solicitud -- no sabe qué es un reporte de órdenes ni una
    reinyección, eso vive en integrations/sodimac/functions/."""

    def __init__(self):
        self._headers = {
            "Ocp-Apim-Subscription-Key": SODIMAC_SUBSCRIPTION_KEY,
            "Content-Type": "application/json",
        }

    def post(self, url, payload):
        response = requests.post(url, headers=self._headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
