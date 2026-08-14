from rest_framework.permissions import BasePermission


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
