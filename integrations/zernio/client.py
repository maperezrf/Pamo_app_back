import requests

from config.constants import ZERNIO_API_KEY, ZERNIO_BASE_URL

TIMEOUT = (5, 30)


class ZernioAPIError(Exception):
    """Zernio respondió con un error HTTP -- incluye el cuerpo de la
    respuesta, donde viene `code` (ej. INVALID_TEMPLATE_PARAMS,
    TEMPLATE_REQUIRED) y, si el rechazo fue de Meta, `platformError`."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class ZernioClient:
    """Conexión con la API de Zernio (https://docs.zernio.com). Solo
    autentica y transporta -- no sabe qué es una plantilla ni un pedido,
    eso vive en integrations/zernio/functions/."""

    def __init__(self):
        self.base_url = ZERNIO_BASE_URL.rstrip("/")

    def post_json(self, path, payload):
        response = requests.post(
            f"{self.base_url}/{path}",
            json=payload,
            headers=self._headers(),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    def upload_file(self, path, content, filename, mime_type):
        response = requests.post(
            f"{self.base_url}/{path}",
            data={"contentType": mime_type},
            files={"file": (filename, content, mime_type)},
            headers=self._headers(),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    def get(self, path, params=None):
        response = requests.get(
            f"{self.base_url}/{path}",
            params=params,
            headers=self._headers(),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    @staticmethod
    def _headers():
        return {"Authorization": f"Bearer {ZERNIO_API_KEY}"}

    @staticmethod
    def _parse(response):
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if not response.ok:
            raise ZernioAPIError(response.status_code, body)
        return body
