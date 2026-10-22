from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from integrations.shopify.functions.get_draft_order import get_draft_order

from .functions.list_quotes import list_quotes
from .functions.quote_links import QuoteLinkExpired, QuoteLinkInvalid, quote_id_from_link
from .functions.quote_pdf import quote_pdf

# Mismos roles que el listado de pedidos (`orders.apis.ORDERS_LIST_ROLES`).
QUOTES_ROLES = ["Admin", "Operaciones"]


class QuoteListAPI(RoleRequiredMixin, APIView):
    """GET /api/quotes/?search=: las cotizaciones (draft orders) más
    recientes de Shopify, en vivo, con el link público de su PDF."""

    allowed_roles = QUOTES_ROLES

    def get(self, request):
        return Response({"quotes": list_quotes(request.query_params.get("search", ""))})


class QuotePdfAPI(RoleRequiredMixin, APIView):
    """GET /api/quotes/<id>/pdf/: descarga el PDF de una cotización. 404 si
    Shopify no la encuentra."""

    allowed_roles = QUOTES_ROLES

    def get(self, request, draft_order_id):
        draft = get_draft_order(draft_order_id)
        if draft is None:
            return Response({"detail": "La cotización no existe en Shopify."}, status=404)
        filename, content = quote_pdf(draft)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class QuotePdfLinkAPI(APIView):
    """GET /api/quotes/pdf/<token>/: el PDF de una cotización por su link
    firmado (`quote_links.quote_link`), para el cliente, que no tiene
    sesión. Público a propósito: la firma es la autorización (solo esa
    cotización, vence a los 90 días). 404 si el link no vale o la cotización
    ya no existe; 410 si venció. Se abre en el navegador (inline)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        try:
            draft_order_id = quote_id_from_link(token)
        except QuoteLinkExpired:
            return _text("El link de la cotización venció. Pídela de nuevo a tu asesor.", 410)
        except QuoteLinkInvalid:
            return _text("Link de cotización inválido.", 404)
        draft = get_draft_order(draft_order_id)
        if draft is None:
            return _text("La cotización ya no existe.", 404)
        filename, content = quote_pdf(draft)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


def _text(message, status):
    return HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
