import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from django.core.management.base import BaseCommand, CommandError

from config.constants import GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET
from integrations.gmail.client import TIMEOUT, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.send"


class Command(BaseCommand):
    help = (
        "Obtiene el GMAIL_REFRESH_TOKEN para enviar correo por la API de Gmail. Se corre UNA vez, en local "
        "(abre el navegador): se inicia sesión con la cuenta remitente y se autoriza solo el permiso de enviar. "
        "Requiere GMAIL_CLIENT_ID y GMAIL_CLIENT_SECRET de un cliente OAuth tipo \"App de escritorio\"."
    )

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8765, help="Puerto local donde Google devuelve el código.")

    def handle(self, *args, **options):
        if not GMAIL_CLIENT_ID or not GMAIL_CLIENT_SECRET:
            raise CommandError("Falta GMAIL_CLIENT_ID o GMAIL_CLIENT_SECRET en el .env.")
        redirect_uri = f"http://127.0.0.1:{options['port']}"
        state = secrets.token_urlsafe(16)
        url = f"{AUTH_URL}?" + urlencode({
            "client_id": GMAIL_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",  # para que Google entregue el refresh token
            "state": state,
        })
        self.stdout.write("Abre este enlace e inicia sesión con la cuenta REMITENTE (si no se abre solo):\n" + url)
        webbrowser.open(url)
        query = _wait_for_redirect(options["port"])
        if query.get("state") != state:
            raise CommandError("La respuesta de Google no corresponde a esta autorización (state distinto).")
        if "code" not in query:
            raise CommandError(f"Google no autorizó: {query.get('error', 'sin código')}.")
        response = requests.post(TOKEN_URL, data={
            "client_id": GMAIL_CLIENT_ID,
            "client_secret": GMAIL_CLIENT_SECRET,
            "code": query["code"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=TIMEOUT)
        data = response.json()
        if response.status_code >= 400 or "refresh_token" not in data:
            raise CommandError(f"No se obtuvo el refresh token: {data.get('error_description') or data.get('error') or data}")
        self.stdout.write(self.style.SUCCESS(
            "Listo. Pon este valor en GMAIL_REFRESH_TOKEN (en el .env y en Railway); no lo compartas:\n"
        ))
        self.stdout.write(data["refresh_token"])


def _wait_for_redirect(port):
    """Atiende una sola visita del navegador en 127.0.0.1 y devuelve los
    parámetros con que Google redirigió."""
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            result.update({key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Listo, ya puedes cerrar esta pestaña y volver a la terminal.".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    while not result:
        server.handle_request()
    server.server_close()
    return result
