from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from .functions.get_order_items import get_order_items
from .functions.get_orders import get_orders


class GetOrdersTests(SimpleTestCase):
    def _call(self, mock_get, **kwargs):
        mock_get.return_value.json.return_value = {"Orders": []}
        mock_get.return_value.raise_for_status.return_value = None
        defaults = {
            "created_after": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "created_before": datetime(2026, 9, 10, tzinfo=timezone.utc),
        }
        return get_orders(**{**defaults, **kwargs})

    def test_rejects_a_limit_outside_1_to_100(self):
        with self.assertRaises(ValueError):
            get_orders(
                created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
                created_before=datetime(2026, 9, 10, tzinfo=timezone.utc),
                limit=0,
            )

    def test_rejects_a_negative_offset(self):
        with self.assertRaises(ValueError):
            get_orders(
                created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
                created_before=datetime(2026, 9, 10, tzinfo=timezone.utc),
                offset=-1,
            )

    @patch("integrations.falabella.client.requests.get")
    def test_signs_the_request_with_hmac_sha256_over_the_canonical_query(self, mock_get):
        self._call(mock_get)
        url = mock_get.call_args.args[0]
        self.assertIn("Signature=", url)
        self.assertIn("Action=GetOrders", url)
        self.assertIn("Format=JSON", url)
        self.assertIn("Version=2.0", url)

    @patch("integrations.falabella.client.requests.get")
    def test_parameters_are_sorted_alphabetically_before_signing(self, mock_get):
        self._call(mock_get)
        url = mock_get.call_args.args[0]
        query = url.split("?", 1)[1]
        names = [pair.split("=", 1)[0] for pair in query.split("&") if not pair.startswith("Signature=")]
        self.assertEqual(names, sorted(names))

    @patch("integrations.falabella.client.requests.get")
    def test_sends_the_configured_user_agent(self, mock_get):
        self._call(mock_get)
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["User-Agent"], "Pamo-App-Backend/1.0")

    @patch("integrations.falabella.client.requests.get")
    def test_status_filter_is_only_included_when_given(self, mock_get):
        self._call(mock_get)
        url_without_status = mock_get.call_args.args[0]
        self.assertNotIn("Status=", url_without_status)

        self._call(mock_get, status="shipped")
        url_with_status = mock_get.call_args.args[0]
        self.assertIn("Status=shipped", url_with_status)

    @patch("integrations.falabella.client.requests.get")
    def test_normalizes_a_real_shaped_response(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {
                    "Orders": {
                        "Order": {
                            "OrderId": "8001456573",
                            "OrderNumber": "3251304046",
                            "CreatedAt": "2026-09-09 15:31:39",
                            "UpdatedAt": "2026-09-10 14:53:41",
                            "Statuses": {"Status": "pending"},
                            "GrandTotal": "542,080.00",
                            "CustomerFirstName": "Ana",
                            "CustomerLastName": "Gomez",
                            "NationalRegistrationNumber": "1234567890",
                            "AddressBilling": {"CustomerEmail": "ana.gomez@example.com"},
                        }
                    }
                }
            }
        }
        result = get_orders(
            created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
            created_before=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(
            result,
            [
                {
                    "order_id": "8001456573",
                    "order_number": "3251304046",
                    "created_at": "2026-09-09 15:31:39",
                    "updated_at": "2026-09-10 14:53:41",
                    "status": "pending",
                    "status_raw": "pending",
                    "total": "542080.00",
                    "customer_first_name": "Ana",
                    "customer_last_name": "Gomez",
                    "customer_identification": "1234567890",
                    "customer_email": "ana.gomez@example.com",
                }
            ],
        )

    @patch("integrations.falabella.client.requests.get")
    def test_customer_fields_default_to_empty_string_when_absent(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {
                    "Orders": {
                        "Order": {
                            "OrderId": "1",
                            "OrderNumber": "1",
                            "CreatedAt": "",
                            "UpdatedAt": "",
                            "Statuses": {"Status": "pending"},
                            "GrandTotal": "0.00",
                        }
                    }
                }
            }
        }
        result = get_orders(
            created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
            created_before=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(result[0]["customer_identification"], "")
        self.assertEqual(result[0]["customer_email"], "")

    @patch("integrations.falabella.client.requests.get")
    def test_returns_an_empty_list_when_falabella_reports_no_orders(self, mock_get):
        # Falabella manda "Orders": "" (string vacío, no un objeto) cuando
        # no hay pedidos en el rango -- confirmado contra una respuesta
        # real el 2026-09-19.
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {"Body": {"Orders": ""}}
        }
        result = get_orders(
            created_after=datetime(2027, 1, 1, tzinfo=timezone.utc),
            created_before=datetime(2027, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result, [])

    @patch("integrations.falabella.client.requests.get")
    def test_falls_back_to_requires_attention_for_an_unknown_status(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {
                    "Orders": {
                        "Order": {
                            "OrderId": "1",
                            "OrderNumber": "1",
                            "CreatedAt": "",
                            "UpdatedAt": "",
                            "Statuses": {"Status": "some_new_status"},
                            "GrandTotal": "0.00",
                        }
                    }
                }
            }
        }
        result = get_orders(
            created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
            created_before=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(result[0]["status"], "requires_attention")


class GetOrderItemsTests(SimpleTestCase):
    def _order_item(self, order_item_id, sku, price="271040.00", status="pending"):
        return {
            "OrderItemId": order_item_id,
            "OrderId": "8001456573",
            "Sku": sku,
            "ShopSku": "157076810",
            "Status": status,
            "ItemPrice": price,
            "PaidPrice": price,
        }

    @patch("integrations.falabella.client.requests.get")
    def test_groups_repeated_rows_of_the_same_sku_into_one_quantity(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {
                    "OrderItems": {
                        "OrderItem": [
                            self._order_item("81975422", "15L907086-N"),
                            self._order_item("81975420", "15L907086-N"),
                        ]
                    }
                }
            }
        }
        result = get_order_items("8001456573")
        self.assertEqual(
            result,
            [
                {
                    "sku": "15L907086-N",
                    "shop_sku": "157076810",
                    "quantity": 2,
                    "price": "271040.00",
                    "status": "pending",
                }
            ],
        )

    @patch("integrations.falabella.client.requests.get")
    def test_uses_the_sku_field_not_shopsku(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {"OrderItems": {"OrderItem": [self._order_item("1", "SKU-REAL-DE-SHOPIFY")]}}
            }
        }
        result = get_order_items("8001456573")
        self.assertEqual(result[0]["sku"], "SKU-REAL-DE-SHOPIFY")

    @patch("integrations.falabella.client.requests.get")
    def test_handles_a_single_item_returned_as_a_bare_dict(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "SuccessResponse": {
                "Body": {"OrderItems": {"OrderItem": self._order_item("1", "ONLY-ONE")}}
            }
        }
        result = get_order_items("8001456573")
        self.assertEqual(
            result,
            [
                {
                    "sku": "ONLY-ONE",
                    "shop_sku": "157076810",
                    "quantity": 1,
                    "price": "271040.00",
                    "status": "pending",
                }
            ],
        )

    @patch("integrations.falabella.client.requests.get")
    def test_sends_the_order_id_to_the_api(self, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"SuccessResponse": {"Body": {"OrderItems": {"OrderItem": []}}}}
        get_order_items("8001456573")
        url = mock_get.call_args.args[0]
        self.assertIn("OrderId=8001456573", url)
        self.assertIn("Action=GetOrderItems", url)
        self.assertIn("Version=1.0", url)
