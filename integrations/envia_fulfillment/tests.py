import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .client import EnviaFulfillmentAPIError, EnviaFulfillmentClient, EnviaFulfillmentWritesDisabled
from .functions.add_order_comment import add_order_comment
from .functions.add_order_tracking import add_order_tracking
from .functions.create_order import EnviaFulfillmentOrderError, create_order
from .functions.find_order import find_order
from .functions.label import label_payload
from .functions.list_warehouses import list_warehouses

CLIENT = "integrations.envia_fulfillment.client"
SINCE = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
PDF = b"%PDF-1.4 " + b"x" * 64 + b" %%EOF"


def _response(payload, status=200, redirect=False):
    response = MagicMock()
    response.status_code = status
    response.is_redirect = redirect
    response.content = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return response


ADDRESS = {
    "first_name": "Ana",
    "last_name": "Gómez",
    "address1": "Calle 1 # 2-3",
    "state_code": "DC",
    "state_name": "Bogotá, D.C.",
    "city": "Bogotá",
    "postal_code": "110111",
    "phone": "3000000000",
}


def _create(**overrides):
    kwargs = dict(
        identifier="mercadolibre-2000015399650969",
        warehouse_id=168,
        products=[{"variant_id": 5842, "quantity": 2, "price": "39124.50"}],
        shipping_address=ADDRESS,
        email="comprador@example.com",
        total="78249.00",
        tracking_number="45678901",
        label_pdf=PDF,
    )
    kwargs.update(overrides)
    return create_order(**kwargs)


@patch(f"{CLIENT}.ENVIA_FULFILLMENT_API_TOKEN", "token-secreto")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_COMPANY_ID", "2156")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_BASE_URL", "https://apifulfillment.envia.com/")
class ClientTests(SimpleTestCase):
    @patch(f"{CLIENT}.requests.request")
    def test_get_resolves_the_company_and_sends_the_bearer_token(self, request):
        request.return_value = _response([])

        EnviaFulfillmentClient().get("/company/{company_id}/warehouse/client")

        method, url = request.call_args.args
        self.assertEqual((method, url), ("GET", "https://apifulfillment.envia.com/company/2156/warehouse/client"))
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer token-secreto")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    @patch(f"{CLIENT}.ENVIA_FULFILLMENT_WRITES_ENABLED", False)
    @patch(f"{CLIENT}.requests.request")
    def test_writes_are_blocked_before_touching_the_network(self, request):
        with self.assertRaises(EnviaFulfillmentWritesDisabled):
            EnviaFulfillmentClient().post("/company/{company_id}/client/order/create", [{}])
        request.assert_not_called()

    @patch(f"{CLIENT}.requests.request")
    def test_http_error_keeps_the_provider_message_but_not_the_token(self, request):
        request.return_value = _response({"message": "warehouse not found"}, status=422)

        with self.assertRaises(EnviaFulfillmentAPIError) as raised:
            EnviaFulfillmentClient().get("/company/{company_id}/warehouse/client")

        self.assertEqual(raised.exception.code, "ENVIA_FULFILLMENT_HTTP_422")
        self.assertIn("warehouse not found", str(raised.exception))
        self.assertNotIn("token-secreto", str(raised.exception))

    @patch(f"{CLIENT}.requests.request")
    def test_redirects_and_non_json_responses_are_rejected(self, request):
        request.return_value = _response(None, status=302, redirect=True)
        with self.assertRaises(EnviaFulfillmentAPIError) as raised:
            EnviaFulfillmentClient().get("/auth/me")
        self.assertEqual(raised.exception.code, "ENVIA_FULFILLMENT_REDIRECT_BLOCKED")

        request.return_value = _response(None)
        request.return_value.content = b"<html>login</html>"
        with self.assertRaises(EnviaFulfillmentAPIError) as raised:
            EnviaFulfillmentClient().get("/auth/me")
        self.assertEqual(raised.exception.code, "ENVIA_FULFILLMENT_RESPONSE_NOT_JSON")

    def test_requires_the_token(self):
        with patch(f"{CLIENT}.ENVIA_FULFILLMENT_API_TOKEN", ""), self.assertRaises(EnviaFulfillmentAPIError):
            EnviaFulfillmentClient()

    @patch(f"{CLIENT}.requests.request")
    def test_company_routes_require_the_company_id(self, request):
        # `with` y no decorador: el parche de la clase se aplicaría después.
        with patch(f"{CLIENT}.ENVIA_FULFILLMENT_COMPANY_ID", ""), self.assertRaises(EnviaFulfillmentAPIError) as raised:
            EnviaFulfillmentClient().get("/company/{company_id}/warehouse/client")
        self.assertEqual(raised.exception.code, "ENVIA_FULFILLMENT_COMPANY_ID_MISSING")
        request.assert_not_called()


@patch(f"{CLIENT}.ENVIA_FULFILLMENT_API_TOKEN", "token-secreto")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_COMPANY_ID", "2156")
class ReadFunctionTests(SimpleTestCase):
    @staticmethod
    def _page(identifiers, created_at="2026-10-08T15:00:00.000Z", size=50):
        orders = [{"orderId": index, "identifier": value, "createdAt": created_at} for index, value in enumerate(identifiers)]
        orders += [{"orderId": 900 + n, "identifier": f"otro-{n}", "createdAt": created_at} for n in range(size - len(orders))]
        return _response({"totalRows": 12361, "orders": orders})

    @patch(f"{CLIENT}.requests.request")
    def test_find_order_matches_the_identifier_exactly(self, request):
        request.return_value = self._page(["160000000", "16200704"])

        found = find_order("16200704", since=SINCE)

        self.assertEqual(found["order_id"], 1)
        self.assertEqual(request.call_args.kwargs["params"], {"page": 1, "limit": 50})
        self.assertEqual(request.call_args.kwargs["headers"]["timezone"], "America/Bogota")

    @patch(f"{CLIENT}.requests.request")
    def test_find_order_walks_pages_until_it_passes_the_order_date(self, request):
        # Página 1 reciente sin coincidencia; página 2 ya es anterior al
        # pedido menos el margen: no existe.
        request.side_effect = [self._page([]), self._page([], created_at="2026-10-01T00:00:00.000Z")]
        self.assertIsNone(find_order("16200704", since=SINCE))
        self.assertEqual(request.call_count, 2)

    @patch(f"{CLIENT}.requests.request")
    def test_find_order_never_says_missing_without_reaching_the_date(self, request):
        request.return_value = self._page([])  # siempre órdenes más nuevas que el pedido
        with patch("integrations.envia_fulfillment.functions.find_order.MAX_PAGES", 3):
            with self.assertRaises(EnviaFulfillmentAPIError) as raised:
                find_order("16200704", since=SINCE)
        self.assertEqual(raised.exception.code, "ENVIA_FULFILLMENT_SEARCH_UNRELIABLE")

    @patch(f"{CLIENT}.requests.request")
    def test_list_warehouses_keeps_id_name_and_city(self, request):
        request.return_value = _response([{"id": 168, "name": "Bodega Envia", "address": {"city": "Bogotá", "address1": "x"}}])
        self.assertEqual(list_warehouses(), [{"id": 168, "name": "Bodega Envia", "city": "Bogotá"}])


@patch(f"{CLIENT}.ENVIA_FULFILLMENT_API_TOKEN", "token-secreto")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_COMPANY_ID", "2156")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_WRITES_ENABLED", True)
@patch("integrations.envia_fulfillment.functions.create_order.ENVIA_FULFILLMENT_SHOP_ID", "1631")
class CreateOrderTests(SimpleTestCase):
    @patch(f"{CLIENT}.requests.request")
    def test_creates_one_order_with_the_guide_attached(self, request):
        request.return_value = _response([{"check": True, "orderId": 406444, "identifier": "mercadolibre-2000015399650969", "warehouseStatus": "PENDING"}])

        result = _create()

        self.assertEqual(result, {"order_id": 406444, "identifier": "mercadolibre-2000015399650969", "warehouse_status": "PENDING"})
        method, url = request.call_args.args
        self.assertEqual((method, url), ("POST", "https://apifulfillment.envia.com/company/2156/client/order/create"))
        [order] = json.loads(request.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(order["orderIdentifier"], "mercadolibre-2000015399650969")
        self.assertEqual(order["shopId"], 1631)
        self.assertEqual(order["warehouseId"], 168)
        self.assertEqual(order["total"], 78249)
        self.assertEqual(order["products"], [{"variantId": 5842, "quantity": 2, "price": 39124.5, "discount": 0}])
        self.assertEqual(order["shippingAddress"]["country"], {"code": "CO", "name": "Colombia"})
        self.assertEqual(order["shippingAddress"]["lastName"], "Gómez")
        [shipment] = order["shipments"]
        self.assertEqual(shipment["trackingNumber"], "45678901")
        self.assertEqual(shipment["label"]["type"], "application/pdf")
        self.assertEqual(base64.b64decode(shipment["label"]["raw"].split(",", 1)[1]), PDF)

    @patch(f"{CLIENT}.requests.request")
    def test_a_response_without_check_is_an_error(self, request):
        request.return_value = _response([{"check": False, "message": "variant not found"}])
        with self.assertRaises(EnviaFulfillmentOrderError):
            _create()

    @patch(f"{CLIENT}.requests.request")
    def test_validates_before_calling_envia(self, request):
        with self.assertRaises(ValueError):
            _create(products=[])
        with self.assertRaises(ValueError):
            _create(email="")
        with self.assertRaises(ValueError):
            _create(label_pdf=b"<html>no es pdf</html>")
        with self.assertRaises(ValueError):
            _create(shipping_address={**ADDRESS, "last_name": ""})
        with self.assertRaises(ValueError):
            _create(shipping_address={**ADDRESS, "state_code": ""})
        request.assert_not_called()

    @patch(f"{CLIENT}.requests.request")
    def test_add_order_tracking_posts_the_guide_to_the_order(self, request):
        request.return_value = _response([{"status": "success"}])

        add_order_tracking(406444, tracking_number="45678901", label_pdf=PDF)

        method, url = request.call_args.args
        self.assertEqual((method, url), ("POST", "https://apifulfillment.envia.com/company/2156/client/order/406444/tracking-numbers"))
        [tracking] = json.loads(request.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(tracking["trackingNumber"], "45678901")
        self.assertTrue(tracking["label"]["raw"].startswith("data:application/pdf;base64,"))


@patch(f"{CLIENT}.ENVIA_FULFILLMENT_API_TOKEN", "token-secreto")
@patch(f"{CLIENT}.ENVIA_FULFILLMENT_COMPANY_ID", "2156")
class OrderCommentTests(SimpleTestCase):
    @patch(f"{CLIENT}.ENVIA_FULFILLMENT_WRITES_ENABLED", True)
    @patch(f"{CLIENT}.requests.request")
    def test_attaches_the_remittance_pdf_to_the_order(self, request):
        request.return_value = _response({"check": True})

        add_order_comment(406444, comment="Remesa", pdf=PDF, filename="relacion.pdf")

        method, url = request.call_args.args
        self.assertEqual((method, url), ("POST", "https://apifulfillment.envia.com/company/2156/client/order/406444/comment"))
        body = json.loads(request.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(body["comment"], "Remesa")
        self.assertEqual(body["file"]["name"], "relacion.pdf")
        self.assertEqual(base64.b64decode(body["file"]["raw"].split(",", 1)[1]), PDF)

    @patch(f"{CLIENT}.ENVIA_FULFILLMENT_WRITES_ENABLED", False)
    @patch(f"{CLIENT}.requests.request")
    def test_is_blocked_without_writes_enabled(self, request):
        with self.assertRaises(EnviaFulfillmentWritesDisabled):
            add_order_comment(1, comment="Remesa")
        request.assert_not_called()


class LabelTests(SimpleTestCase):
    def test_rejects_anything_that_is_not_a_pdf(self):
        with self.assertRaises(ValueError):
            label_payload(b"GIF89a", "guia.pdf")
        with self.assertRaises(ValueError):
            label_payload("no-bytes", "guia.pdf")
