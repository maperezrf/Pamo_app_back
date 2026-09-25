import json
from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .client import MadecentroAPIError, MadecentroClient
from .functions.get_order import get_order
from .functions.list_orders import list_orders

BASE_URL = "https://shipturtle.test/api/v1"


def _raw_order(**overrides):
    """Forma real (reducida) de `GET /orders/{id}` -> `data`, verificada el
    2026-09-24. Datos del comprador inventados."""
    data = {
        "id": 17707107,
        "order_id": 1925041443712,
        "name": "#1001114236",
        "order_number": "114236",
        "financial_status": "paid",
        "cancelled_at": None,
        "email": "ana@example.com",
        "customer_email": "ana@example.com",
        "customer_phone": "3000000000",
        "customer_first_name": "Ana",
        "customer_last_name": "Gómez",
        "billing_address": {
            "first_name": "Ana",
            "last_name": "Gómez",
            "name": "Ana Gómez",
            "company": "",
            "address1": "Calle 1 # 2-3",
            "address2": "Apto 4",
            "city": "Medellín",
            "province": "Antioquia",
            "phone": "3001234567",
        },
        "line_items": [{"sku": "PMO-028-MP", "quantity": 2, "price": 221335, "vendor_id": None}],
    }
    data.update(overrides)
    return data


def _response(status_code=200, payload=None, *, is_redirect=False, invalid_json=False):
    response = Mock(status_code=status_code, is_redirect=is_redirect)
    if invalid_json:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload if payload is not None else {}
    return response


@patch("integrations.madecentro.client.MADECENTRO_API_BASE_URL", BASE_URL)
@patch("integrations.madecentro.client.requests.get")
class MadecentroClientTests(SimpleTestCase):
    def test_sends_bearer_token_to_the_configured_base_url(self, get):
        get.return_value = _response(payload={"ok": True})

        result = MadecentroClient("TOKEN").get("/orders/1")

        self.assertEqual(result, {"ok": True})
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/orders/1")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer TOKEN")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIsNotNone(kwargs["timeout"])

    def test_missing_token_fails_without_calling_the_api(self, get):
        with self.assertRaisesMessage(MadecentroAPIError, "MADECENTRO_TOKEN_MISSING"):
            MadecentroClient("")
        get.assert_not_called()

    def test_http_error_raises_a_code_without_the_token(self, get):
        for status in (401, 500):
            get.return_value = _response(status_code=status)
            with self.assertRaises(MadecentroAPIError) as caught:
                MadecentroClient("SECRET-TOKEN").get("/orders/1")
            self.assertEqual(str(caught.exception), f"MADECENTRO_HTTP_{status}")
            self.assertNotIn("SECRET-TOKEN", str(caught.exception))

    def test_non_json_body_is_invalid(self, get):
        get.return_value = _response(invalid_json=True)

        with self.assertRaisesMessage(MadecentroAPIError, "MADECENTRO_RESPONSE_INVALID"):
            MadecentroClient("TOKEN").get("/orders/1")

    def test_redirect_is_blocked(self, get):
        get.return_value = _response(status_code=302, is_redirect=True)

        with self.assertRaisesMessage(MadecentroAPIError, "MADECENTRO_REDIRECT_BLOCKED"):
            MadecentroClient("TOKEN").get("/orders/1")


@patch("integrations.madecentro.client.MADECENTRO_API_BASE_URL", BASE_URL)
@patch("integrations.madecentro.client.requests.get")
class MadecentroOrderFunctionsTests(SimpleTestCase):
    @patch("integrations.madecentro.functions.get_order.MADECENTRO_ORDERS_TOKEN", "ORDERS")
    def test_get_order_reads_the_order_with_the_orders_token_and_normalizes_it(self, get):
        get.return_value = _response(payload={"data": _raw_order()})

        order = get_order(17707107)

        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/orders/17707107")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ORDERS")
        self.assertEqual(
            order,
            {
                "order_id": "17707107",
                "order_number": "1001114236",
                "financial_status": "paid",
                "cancelled": False,
                "customer_first_name": "Ana",
                "customer_last_name": "Gómez",
                "customer_email": "ana@example.com",
                "customer_phone": "3001234567",
                "customer_address": "Calle 1 # 2-3, Apto 4",
                "customer_city": "Medellín",
                "customer_region": "Antioquia",
                "items": [{"sku": "PMO-028-MP", "quantity": 2, "price": "221335"}],
            },
        )

    @patch("integrations.madecentro.functions.get_order.MADECENTRO_ORDERS_TOKEN", "ORDERS")
    def test_get_order_company_buyer_uses_the_billing_name_and_flags_cancellation(self, get):
        raw = _raw_order(cancelled_at="2026-09-24 10:00:00")
        raw["billing_address"].update(first_name="", last_name="", name="EMPRESA SAS")
        raw.update(customer_first_name="", customer_last_name="")
        get.return_value = _response(payload={"data": raw})

        order = get_order(17707107)

        self.assertEqual((order["customer_first_name"], order["customer_last_name"]), ("EMPRESA SAS", ""))
        self.assertTrue(order["cancelled"])

    @patch("integrations.madecentro.functions.list_orders.MADECENTRO_ORDERS_TOKEN", "ORDERS")
    def test_list_orders_builds_the_shipturtle_query(self, get):
        get.return_value = _response(
            payload={"data": [{"id": 5, "name": "#1001", "financial_status": "voided"}], "count": 7}
        )

        result = list_orders(date(2023, 11, 1), date(2023, 11, 16), page=2, limit=1)

        self.assertEqual(
            result, {"orders": [{"order_id": "5", "order_number": "1001", "financial_status": "voided"}], "count": 7}
        )

        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/all-orders/fetchData")
        params = kwargs["params"]
        self.assertEqual(
            json.loads(params["query"]),
            {
                "order_date": {"startDate": "11/1/2023", "endDate": "11/16/2023"},
                "type_of_order": "forward order",
            },
        )
        self.assertEqual(
            {key: params[key] for key in ("limit", "ascending", "page", "byColumn")},
            {"limit": 1, "ascending": 0, "page": 2, "byColumn": 1},
        )

    @patch("integrations.madecentro.functions.get_order.MADECENTRO_ORDERS_TOKEN", "")
    def test_get_order_without_token_fails_before_the_request(self, get):
        with self.assertRaisesMessage(MadecentroAPIError, "MADECENTRO_TOKEN_MISSING"):
            get_order(1)
        get.assert_not_called()
