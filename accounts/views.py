from django.contrib.auth import get_user_model, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.constants import DEBUG, GOOGLE_CLIENT_ID, LOCAL_DEMO_AUTH_ENABLED

from .functions.build_menu import build_menu_for_user
from .models import AllowedEmail
from .permissions import RoleRequiredMixin

User = get_user_model()


@require_GET
@ensure_csrf_cookie
def csrf(request):
    # El token va también en el body (no solo en la cookie) porque en
    # producción frontend y backend quedan en hosts distintos
    # (*.up.railway.app): el JS del frontend no puede leer una cookie de
    # otro dominio, así que la única forma de que arme el header
    # X-CSRFToken es leyendo este valor de la respuesta.
    return JsonResponse({"detail": "ok", "csrftoken": get_token(request)})


class GoogleLoginAPI(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        credential = request.data.get("credential")
        if not credential:
            return Response({"authorized": False, "reason": "missing_credential"}, status=400)

        try:
            payload = id_token.verify_oauth2_token(
                credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        except ValueError:
            return Response({"authorized": False, "reason": "invalid_token"}, status=400)

        if not payload.get("email_verified"):
            return Response({"authorized": False, "reason": "email_not_verified"}, status=403)

        email = payload["email"].strip().lower()
        if not AllowedEmail.objects.filter(email=email).exists():
            return Response({"authorized": False, "reason": "not_allowed"}, status=403)

        user, _ = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "first_name": payload.get("given_name", "")},
        )
        login(request, user)

        return Response({
            "authorized": True,
            "user": {"email": user.email, "name": payload.get("name", user.email)},
        })


class LocalDemoLoginAPI(APIView):
    """Acceso explícito para la copia local aislada.

    La doble condición impide que el endpoint funcione si una variable queda
    habilitada accidentalmente fuera de DEBUG. No recibe correo ni permite
    escoger identidad desde el navegador.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        if not (DEBUG and LOCAL_DEMO_AUTH_ENABLED):
            return Response({"authorized": False, "reason": "disabled"}, status=404)
        user = User.objects.filter(username="operador.local@pamo.test").first()
        if not user:
            return Response(
                {"authorized": False, "reason": "run_seed_orders_local"},
                status=409,
            )
        login(request, user)
        return Response(
            {
                "authorized": True,
                "user": {"email": user.email, "name": "Operador local"},
                "localMode": True,
            }
        )


class MeAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"email": request.user.email, "username": request.user.username})


class LogoutAPI(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"detail": "logged_out"})


class MenuAPI(APIView):
    """Árbol de módulos/submódulos de navegación, filtrado por los roles
    del usuario logueado -- ver accounts/menu_config.py."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_menu_for_user(request.user))


class PingAdminAPI(RoleRequiredMixin, APIView):
    """Endpoint de prueba para verificar el mixin de roles de punta a punta.
    Borrar (o dejar de referencia) una vez existan endpoints reales."""
    allowed_roles = ["Admin"]

    def get(self, request):
        return Response({"is_admin": True})
