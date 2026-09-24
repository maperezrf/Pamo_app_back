"""Conexión de la cuenta de vendedor de Mercado Libre (OAuth2) desde el
navegador. Solo `Admin`: quien conecta decide qué cuenta de Mercado Libre
usa el backend.

Flujo: el admin, con sesión iniciada, abre `connect/` -> se le redirige a
Mercado Libre -> acepta -> Mercado Libre redirige el navegador a
`callback/` (MERCADOLIBRE_REDIRECT_URI) con `code` y `state` -> se valida
`state` contra la sesión y se guarda el token (MercadoLibreToken).
"""

import hmac
import secrets
import time

from django.http import HttpResponseRedirect
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .client import MercadoLibreAPIError, MercadoLibreAuthError, MercadoLibreClient

STATE_SESSION_KEY = "mercadolibre_oauth_state"
# El `code` de Mercado Libre vence en minutos; un `state` más viejo que
# esto no corresponde a un intento en curso.
STATE_MAX_AGE_SECONDS = 600


class MercadoLibreConnectView(RoleRequiredMixin, APIView):
    """Inicia la conexión: guarda un `state` de un solo uso en la sesión y
    redirige a la autorización de Mercado Libre."""

    allowed_roles = ["Admin"]

    def get(self, request):
        state = secrets.token_urlsafe(32)
        try:
            url = MercadoLibreClient.authorization_url(state)
        except MercadoLibreAuthError as error:
            return Response({"detail": str(error)}, status=503)
        request.session[STATE_SESSION_KEY] = {"value": state, "created_at": int(time.time())}
        return HttpResponseRedirect(url)


class MercadoLibreCallbackView(RoleRequiredMixin, APIView):
    """Recibe la redirección de Mercado Libre. Exige la misma sesión que
    inició el flujo: el `state` se consume siempre (incluso si algo falla),
    así un callback no se puede repetir."""

    allowed_roles = ["Admin"]

    def get(self, request):
        expected = request.session.pop(STATE_SESSION_KEY, None)
        if not _state_is_valid(expected, request.query_params.get("state", "")):
            return Response({"detail": "state inválido o vencido; iniciar de nuevo la conexión"}, status=400)
        if "error" in request.query_params:
            return Response(
                {"detail": f"Mercado Libre no autorizó la conexión: {request.query_params['error']}"}, status=400
            )
        code = request.query_params.get("code", "")
        if not code:
            return Response({"detail": "falta el parámetro code"}, status=400)
        try:
            seller_id = MercadoLibreClient().exchange_code(code)
        except MercadoLibreAuthError as error:
            return Response({"detail": str(error)}, status=400)
        except MercadoLibreAPIError as error:
            return Response({"detail": f"Mercado Libre respondió HTTP {error.status_code}"}, status=502)
        return Response({"connected": True, "seller_id": seller_id})


def _state_is_valid(expected, received):
    if not isinstance(expected, dict) or not received:
        return False
    if time.time() - expected.get("created_at", 0) > STATE_MAX_AGE_SECONDS:
        return False
    return hmac.compare_digest(str(expected.get("value", "")), received)
