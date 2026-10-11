import requests

from config.constants import SODIMAC_RPA_URL

TIMEOUT = (5, 20)


class SodimacRpaError(Exception):
    """El RPA respondió con error o no está configurado."""


class SodimacRpaBusy(SodimacRpaError):
    """El RPA ya tiene una corrida en curso (409): solo corre una a la vez."""


class SodimacRpaClient:
    """Conexión con el RPA de guías de Sodimac (repositorio `maperezrf/rpa_sodimac`,
    panel FastAPI en Railway). Solo transporta. El RPA no exige credenciales
    a quien lo llama; las suyas hacia este backend van en el webhook."""

    def __init__(self):
        if not SODIMAC_RPA_URL:
            raise SodimacRpaError("SODIMAC_RPA_URL no está configurada.")
        self.base_url = SODIMAC_RPA_URL.rstrip("/")

    def post(self, path, payload=None):
        response = requests.post(f"{self.base_url}{path}", json=payload, timeout=TIMEOUT, allow_redirects=False)
        return self._parse(response)

    def get(self, path):
        response = requests.get(f"{self.base_url}{path}", timeout=TIMEOUT, allow_redirects=False)
        return self._parse(response)

    @staticmethod
    def _parse(response):
        if response.status_code == 409:
            raise SodimacRpaBusy(response.text[:300])
        if response.status_code >= 400:
            raise SodimacRpaError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as error:
            raise SodimacRpaError(f"Respuesta no JSON: {response.text[:300]}") from error
