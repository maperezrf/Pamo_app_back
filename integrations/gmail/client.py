import time

import requests

from config.constants import GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = (5, 20)


class GmailAPIError(Exception):
    """Google respondió con un error HTTP. Incluye el cuerpo (ahí viene el
    motivo, p. ej. `invalid_grant` si el refresh token se revocó)."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class GmailClient:
    """Conexión con la API de Gmail de la cuenta que autorizó el refresh
    token. Solo autentica y transporta. El access token dura ~1 h y se
    renueva con el refresh token, que es fijo (Google no lo rota), así que
    basta guardarlo en memoria del proceso."""

    _access_token = ""
    _expires_at = 0.0

    def post(self, path, payload):
        response = requests.post(
            f"{GMAIL_BASE_URL}/{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        return self._parse(response)

    @classmethod
    def _token(cls):
        if cls._access_token and time.monotonic() < cls._expires_at:
            return cls._access_token
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": GMAIL_CLIENT_ID,
                "client_secret": GMAIL_CLIENT_SECRET,
                "refresh_token": GMAIL_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        data = cls._parse(response)
        cls._access_token = data["access_token"]
        cls._expires_at = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return cls._access_token

    @staticmethod
    def _parse(response):
        try:
            body = response.json()
        except ValueError:
            body = response.text[:500]
        if response.status_code >= 400:
            raise GmailAPIError(response.status_code, body)
        return body
