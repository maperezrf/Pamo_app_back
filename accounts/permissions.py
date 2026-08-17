import hmac

from rest_framework.permissions import BasePermission

from config.constants import MCP_API_KEY


class HasRole(BasePermission):
    """Autorizado si es superusuario, si `view.allowed_roles` está vacío
    (solo exige sesión), o si el usuario pertenece a alguno de esos grupos
    (comparación por nombre, nunca por id)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_superuser:
            return True
        allowed_roles = getattr(view, "allowed_roles", None)
        if not allowed_roles:
            return True
        return request.user.groups.filter(name__in=allowed_roles).exists()


class RoleRequiredMixin:
    """Mixin para APIView: declarar `allowed_roles = ["Admin", "Operaciones"]`
    en la vista. Sin la lista, solo exige sesión iniciada."""

    permission_classes = [HasRole]
    allowed_roles = []


class HasValidApiKey(BasePermission):
    """Autoriza a un consumidor máquina-a-máquina interno (ej. servidor MCP)
    que no tiene sesión de Django ni pertenece a un Group -- ver
    `GOVERNANCE.md` §4.4. Compara el header `X-API-Key` contra el secreto
    único en `config/constants.py`, en tiempo constante."""

    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-Key", "")
        return bool(api_key) and hmac.compare_digest(api_key, MCP_API_KEY)


class ApiKeyRequiredMixin:
    """Mixin para APIView consumidas por un cliente interno máquina-a-máquina
    (no un usuario con sesión, no un webhook de proveedor externo) -- ver
    `GOVERNANCE.md` §4.4."""

    permission_classes = [HasValidApiKey]
