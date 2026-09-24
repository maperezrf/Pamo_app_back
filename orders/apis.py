from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.functions.import_falabella_orders import import_falabella_orders


class GetOrdersTest(APIView):
    permission_classes = [AllowAny]  # SOLO PRUEBA LOCAL — no hacer commit

    def get(self, request):
        import_falabella_orders(params={"limit": 1})
        return Response({"ok": True})