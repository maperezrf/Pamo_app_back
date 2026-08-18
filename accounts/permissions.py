import hmac

from rest_framework.permissions import BasePermission

from config.constants import MCP_API_KEY


def user_matches_roles(user, roles):
    """Superusuario o lista de roles vacía: acceso libre. Si no, exige que
    el usuario pertenezca a alguno de esos `Group` (comparación por
    nombre). Única fuente de verdad de "qué significa tener acceso a X" --
    la usan tanto `HasRole` (permiso real del endpoint) como el armado del
    menú de navegación, para que nunca queden desalineados."""

    if user.is_superuser:
        return True
    if not roles:
        return True
    return user.groups.filter(name__in=roles).exists()


class HasRole(BasePermission):
    """Autorizado si es superusuario, si `view.allowed_roles` está vacío
    (solo exige sesión), o si el usuario pertenece a alguno de esos grupos
    (comparación por nombre, nunca por id)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        allowed_roles = getattr(view, "allowed_roles", None)
        return user_matches_roles(request.user, allowed_roles)


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
