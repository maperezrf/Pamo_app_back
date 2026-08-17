from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.github import GitHubClient

from .functions.procesar_webhook_github_merge import procesar_webhook_github_merge


class GitHubMergeWebhook(APIView):
    """Recibe el evento `pull_request` de GitHub y actualiza los campos de
    merge del `GovernancePrototipo` correspondiente. Sin Celery (no está
    instalado en este proyecto): actualizar un registro existente es rápido
    y síncrono, dentro de la excepción de GOVERNANCE.md §9 ("Cuándo NO usar
    Celery")."""

    permission_classes = [AllowAny]

    def post(self, request):
        firma_ok = GitHubClient().verify_webhook_signature(
            request.body, request.headers.get("X-Hub-Signature-256")
        )
        if not firma_ok:
            return Response(status=401)

        procesar_webhook_github_merge(
            request.data, request.headers.get("X-GitHub-Delivery", "")
        )
        return Response(status=200)
