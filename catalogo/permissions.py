from django.conf import settings
from rest_framework.permissions import BasePermission


class LocalOrAuthenticatedCatalogAccess(BasePermission):
    """Permite el laboratorio sin OAuth solo con DEBUG local.

    En cualquier ambiente no local vuelve a exigir sesión. No habilita
    integraciones ni escrituras externas.
    """

    def has_permission(self, request, view):
        return bool(settings.DEBUG) or bool(request.user and request.user.is_authenticated)
