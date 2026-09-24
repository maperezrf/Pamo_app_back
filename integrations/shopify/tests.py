import base64
import hashlib
import hmac
from unittest.mock import patch

from django.test import SimpleTestCase

from config.constants import SHOPIFY_WEBHOOK_SECRET

from .client import ShopifyClient
from .functions.create_customer import ShopifyCustomerCreationError, create_customer
from .functions.create_customer_address import (
    ShopifyCustomerAddressError,
    create_customer_address,
)
from .functions.create_order import ShopifyOrderCreationError, create_order
from .functions.get_variant_by_sku import get_variant_by_sku
from .functions.get_variant_inventory_by_sku import get_variant_inventory_by_sku
from .functions.list_customers_page import list_customers_page
from .functions.update_customer_address import update_customer_address


def _mock_response(mock_post, edges):
    mock_post.return_value.json.return_value = {
        "data": {"productVariants": {"edges": edges}}
    }
    mock_post.return_value.raise_for_status.return_value = None


def _node(variant_id, sku, product_id="gid://shopify/Product/1", title="Product"):
    return {
        "node": {
            "id": variant_id,
            "sku": sku,
            "product": {"id": product_id, "title": title},
        }
    }


class GetVariantBySkuTests(SimpleTestCase):
    @patch("integrations.shopify.client.requests.post")
    def test_returns_id_without_prefix_when_sku_matches_exactly(self, mock_post):
        _mock_response(
            mock_post,
            [_node("gid://shopify/ProductVariant/123", "ABC-1")],
        )
        self.assertEqual(get_variant_by_sku("ABC-1"), "123")

    @patch("integrations.shopify.client.requests.post")
    def test_returns_none_when_there_are_no_matches(self, mock_post):
        _mock_response(mock_post, [])
        self.assertIsNone(get_variant_by_sku("NOT-FOUND"))

    @patch("integrations.shopify.client.requests.post")
    def test_does_not_trust_a_partial_sku_match(self, mock_post):
        _mock_response(
            mock_post,
            [_node("gid://shopify/ProductVariant/1", "ABC-1-OTHER")],
        )
        self.assertIsNone(get_variant_by_sku("ABC-1"))

    @patch("integrations.shopify.client.requests.post")
    def test_sends_the_sku_as_a_variable_not_inside_the_graphql_document(self, mock_post):
        _mock_response(mock_post, [])
        get_variant_by_sku('ABC" OR sku:*')
        _, kwargs = mock_post.call_args
        self.assertNotIn('ABC" OR sku:*', kwargs["json"]["query"])
        self.assertEqual(kwargs["json"]["variables"]["query"], 'sku:ABC" OR sku:*')


def _level(location_id, name, available):
    return {
        "node": {
            "location": {"id": f"gid://shopify/Location/{location_id}", "name": name},
            "quantities": [{"name": "available", "quantity": available}],
        }
    }


def _inventory_node(sku="16164783", tracked=True, levels=None):
    node = _node("gid://shopify/ProductVariant/47993005441301", sku)
    node["node"]["inventoryItem"] = {
        "tracked": tracked,
        "inventoryLevels": {"edges": levels if levels is not None else []},
    }
    return node


class GetVariantInventoryBySkuTests(SimpleTestCase):
    @patch("integrations.shopify.client.requests.post")
    def test_normalizes_the_real_response_shape(self, mock_post):
        # Forma verificada contra la cuenta real el 2026-09-23.
        _mock_response(
            mock_post,
            [
                _inventory_node(
                    levels=[
                        _level("94018535701", "Proveedores", 45),
                        _level("97615380757", "Bodega Envia", 25),
                    ]
                )
            ],
        )
        self.assertEqual(
            get_variant_inventory_by_sku("16164783"),
            {
                "variant_id": "47993005441301",
                "sku": "16164783",
                "tracked": True,
                "locations": [
                    {"location_id": "94018535701", "name": "Proveedores", "available": 45},
                    {"location_id": "97615380757", "name": "Bodega Envia", "available": 25},
                ],
            },
        )

    @patch("integrations.shopify.client.requests.post")
    def test_returns_none_without_an_exact_sku_match(self, mock_post):
        _mock_response(mock_post, [_inventory_node(sku="16164783-OTHER")])
        self.assertIsNone(get_variant_inventory_by_sku("16164783"))

    @patch("integrations.shopify.client.requests.post")
    def test_returns_none_when_there_are_no_matches(self, mock_post):
        _mock_response(mock_post, [])
        self.assertIsNone(get_variant_inventory_by_sku("16164783"))

    @patch("integrations.shopify.client.requests.post")
    def test_reports_untracked_items(self, mock_post):
        _mock_response(mock_post, [_inventory_node(tracked=False)])
        result = get_variant_inventory_by_sku("16164783")
        self.assertFalse(result["tracked"])
        self.assertEqual(result["locations"], [])

    @patch("integrations.shopify.client.requests.post")
    def test_tolerates_a_missing_inventory_item(self, mock_post):
        _mock_response(mock_post, [_node("gid://shopify/ProductVariant/1", "16164783")])
        result = get_variant_inventory_by_sku("16164783")
        self.assertFalse(result["tracked"])
        self.assertEqual(result["locations"], [])

    @patch("integrations.shopify.client.requests.post")
    def test_available_defaults_to_zero_when_not_reported(self, mock_post):
        level = _level("1", "Baru", 3)
        level["node"]["quantities"] = [{"name": "on_hand", "quantity": 3}]
        _mock_response(mock_post, [_inventory_node(levels=[level])])
        self.assertEqual(get_variant_inventory_by_sku("16164783")["locations"][0]["available"], 0)


class CreateOrderTests(SimpleTestCase):
    def _items(self):
        return [{"variant_id": "123", "quantity": 2, "price": "10.50"}]

    def test_rejects_an_invalid_financial_status_before_calling_the_api(self):
        with self.assertRaises(ValueError):
            create_order(
                items=self._items(), customer_id="1", financial_status="NOT_A_REAL_STATUS"
            )

    @patch("integrations.shopify.client.requests.post")
    def test_returns_the_order_id_and_name_without_the_gid_prefix(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "orderCreate": {
                    "order": {
                        "id": "gid://shopify/Order/999",
                        "name": "#1001",
                        "displayFinancialStatus": "PENDING",
                    },
                    "userErrors": [],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        result = create_order(items=self._items(), customer_id="1", financial_status="PENDING")
        self.assertEqual(result, {"order_id": "999", "order_name": "#1001"})

    @patch("integrations.shopify.client.requests.post")
    def test_sends_variant_and_customer_ids_with_the_gid_prefix(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {"orderCreate": {"order": {"id": "gid://shopify/Order/1", "name": "#1"}, "userErrors": []}}
        }
        mock_post.return_value.raise_for_status.return_value = None
        create_order(items=self._items(), customer_id="42", financial_status="PENDING")
        _, kwargs = mock_post.call_args
        order_input = kwargs["json"]["variables"]["order"]
        self.assertEqual(
            order_input["lineItems"][0]["variantId"], "gid://shopify/ProductVariant/123"
        )
        self.assertEqual(order_input["customer"]["toAssociate"]["id"], "gid://shopify/Customer/42")

    @patch("integrations.shopify.client.requests.post")
    def test_raises_when_shopify_reports_user_errors(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "orderCreate": {
                    "order": None,
                    "userErrors": [{"field": ["lineItems", "0", "variantId"], "message": "Variant not found"}],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        with self.assertRaises(ShopifyOrderCreationError):
            create_order(items=self._items(), customer_id="1", financial_status="PENDING")

    @patch("integrations.shopify.client.requests.post")
    def test_raises_on_a_top_level_graphql_error_instead_of_a_silent_none(self, mock_post):
        mock_post.return_value.json.return_value = {
            "errors": [{"message": "Access denied for field lineItems on type OrderCreateOrderInput"}]
        }
        mock_post.return_value.raise_for_status.return_value = None
        with self.assertRaises(Exception):
            create_order(items=self._items(), customer_id="1", financial_status="PENDING")


class CreateCustomerTests(SimpleTestCase):
    def test_rejects_when_neither_email_nor_phone_is_given(self):
        with self.assertRaises(ValueError):
            create_customer(first_name="Ana")

    @patch("integrations.shopify.client.requests.post")
    def test_returns_the_customer_id_without_the_gid_prefix(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customerCreate": {
                    "customer": {"id": "gid://shopify/Customer/321", "email": "ana@example.com"},
                    "userErrors": [],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        self.assertEqual(create_customer(email="ana@example.com"), "321")

    @patch("integrations.shopify.client.requests.post")
    def test_raises_when_shopify_reports_user_errors(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customerCreate": {
                    "customer": None,
                    "userErrors": [{"field": ["email"], "message": "Email is invalid"}],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        with self.assertRaises(ShopifyCustomerCreationError):
            create_customer(email="not-an-email")


class CreateCustomerAddressTests(SimpleTestCase):
    @patch("integrations.shopify.client.requests.post")
    def test_returns_the_address_id_exactly_as_shopify_sent_it(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customerAddressCreate": {
                    "address": {"id": "gid://shopify/MailingAddress/1?model_name=CustomerAddress", "company": "123"},
                    "userErrors": [],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        result = create_customer_address(customer_id="42", company="123")
        self.assertEqual(result, "gid://shopify/MailingAddress/1?model_name=CustomerAddress")

    @patch("integrations.shopify.client.requests.post")
    def test_sends_the_customer_id_with_the_gid_prefix(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {"customerAddressCreate": {"address": {"id": "x"}, "userErrors": []}}
        }
        mock_post.return_value.raise_for_status.return_value = None
        create_customer_address(customer_id="42", company="123")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["variables"]["customerId"], "gid://shopify/Customer/42")

    @patch("integrations.shopify.client.requests.post")
    def test_raises_when_shopify_reports_user_errors(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customerAddressCreate": {
                    "address": None,
                    "userErrors": [{"field": ["address", "company"], "message": "too long"}],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        with self.assertRaises(ShopifyCustomerAddressError):
            create_customer_address(customer_id="42", company="1" * 999)


class UpdateCustomerAddressTests(SimpleTestCase):
    def test_rejects_when_no_field_is_given(self):
        with self.assertRaises(ValueError):
            update_customer_address(customer_id="42", address_id="gid://shopify/MailingAddress/1")

    @patch("integrations.shopify.client.requests.post")
    def test_sends_the_address_id_unmodified(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {"customerAddressUpdate": {"address": {"id": "opaque-id"}, "userErrors": []}}
        }
        mock_post.return_value.raise_for_status.return_value = None
        update_customer_address(customer_id="42", address_id="opaque-id", company="123")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["variables"]["addressId"], "opaque-id")
        self.assertEqual(kwargs["json"]["variables"]["address"], {"company": "123"})


class ListCustomersPageTests(SimpleTestCase):
    @patch("integrations.shopify.client.requests.post")
    def test_normalizes_a_page_and_reports_pagination_state(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customers": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "abc123"},
                    "edges": [
                        {
                            "node": {
                                "id": "gid://shopify/Customer/1",
                                "email": "ana@example.com",
                                "phone": "3001234567",
                                "firstName": "Ana",
                                "lastName": "Gomez",
                                "updatedAt": "2026-09-15T00:00:00Z",
                                "defaultAddress": {
                                    "id": "gid://shopify/MailingAddress/1",
                                    "company": "1234567890",
                                },
                            }
                        }
                    ],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        result = list_customers_page(cursor="prev-cursor")
        self.assertEqual(result["has_next_page"], True)
        self.assertEqual(result["end_cursor"], "abc123")
        self.assertEqual(
            result["customers"],
            [
                {
                    "shopify_id": "1",
                    "email": "ana@example.com",
                    "phone": "3001234567",
                    "first_name": "Ana",
                    "last_name": "Gomez",
                    "shopify_updated_at": "2026-09-15T00:00:00Z",
                    "default_address_id": "gid://shopify/MailingAddress/1",
                    "identification": "1234567890",
                }
            ],
        )

    @patch("integrations.shopify.client.requests.post")
    def test_defaults_identification_and_address_id_to_empty_string_without_default_address(self, mock_post):
        mock_post.return_value.json.return_value = {
            "data": {
                "customers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "edges": [
                        {
                            "node": {
                                "id": "gid://shopify/Customer/2",
                                "email": "",
                                "phone": "",
                                "firstName": "",
                                "lastName": "",
                                "updatedAt": "2026-09-15T00:00:00Z",
                                "defaultAddress": None,
                            }
                        }
                    ],
                }
            }
        }
        mock_post.return_value.raise_for_status.return_value = None
        result = list_customers_page()
        self.assertEqual(result["customers"][0]["identification"], "")
        self.assertEqual(result["customers"][0]["default_address_id"], "")


class VerifyWebhookSignatureTests(SimpleTestCase):
    def _sign(self, body):
        digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def test_accepts_a_correctly_signed_body(self):
        body = b'{"id": 1}'
        self.assertTrue(ShopifyClient.verify_webhook_signature(body, self._sign(body)))

    def test_rejects_a_tampered_body(self):
        body = b'{"id": 1}'
        signature = self._sign(body)
        self.assertFalse(ShopifyClient.verify_webhook_signature(b'{"id": 2}', signature))

    def test_rejects_a_missing_signature(self):
        self.assertFalse(ShopifyClient.verify_webhook_signature(b"{}", ""))
