from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import ApiKeyRequiredMixin, RoleOrApiKeyRequiredMixin

from .models import GovernancePrototipo
from .serializers import GovernancePrototipoSerializer


class GovernancePrototipoAdminListAPI(RoleOrApiKeyRequiredMixin, APIView):
    """Lectura de la lista completa -- para el frontend (usuario con rol
    Admin, sesión de Django) y para el servidor MCP (API key). La creación
    y modificación las hace el MCP a través de
    `GovernancePrototipoListCreateAPI` / `GovernancePrototipoDetailAPI`
    (API key), no este endpoint."""

    allowed_roles = ["Admin"]

    def get(self, request):
        prototipos = GovernancePrototipo.objects.all()
        serializer = GovernancePrototipoSerializer(prototipos, many=True)
        return Response(serializer.data)


class GovernancePrototipoListCreateAPI(ApiKeyRequiredMixin, APIView):
    def get(self, request):
        prototipos = GovernancePrototipo.objects.all()
        serializer = GovernancePrototipoSerializer(prototipos, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = GovernancePrototipoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)


class GovernancePrototipoDetailAPI(ApiKeyRequiredMixin, APIView):
    def get(self, request, id):
        prototipo = get_object_or_404(GovernancePrototipo, id=id)
        serializer = GovernancePrototipoSerializer(prototipo)
        return Response(serializer.data)

    def patch(self, request, id):
        prototipo = get_object_or_404(GovernancePrototipo, id=id)
        serializer = GovernancePrototipoSerializer(prototipo, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
