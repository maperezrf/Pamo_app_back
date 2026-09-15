import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from config.constants import SHOPIFY_WEBHOOK_SECRET
from orchestrator.models import ProcessExecution

from .functions.find_by_identification import find_by_identification
from .functions.parse_customer_webhook import parse_customer_webhook
from .functions.process_customer_webhook import process_customer_webhook
from .functions.reconcile_from_shopify import reconcile_from_shopify
from .functions.set_identification import set_identification
from .functions.upsert_from_shopify_customer import upsert_from_shopify_customer
from .models import ShopifyCustomer


class FindByIdentificationTests(TestCase):
    def test_returns_none_for_empty_identification(self):
        self.assertIsNone(find_by_identification(""))

    def test_returns_none_when_no_match(self):
        self.assertIsNone(find_by_identification("1234567890"))

    def test_returns_the_matching_customer(self):
        customer = ShopifyCustomer.objects.create(shopify_id="1", identification="1234567890")
        self.assertEqual(find_by_identification("1234567890"), customer)

    def test_returns_the_most_recently_updated_among_duplicates(self):
        ShopifyCustomer.objects.create(
            shopify_id="1",
            identification="1234567890",
            shopify_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer = ShopifyCustomer.objects.create(
            shopify_id="2",
            identification="1234567890",
            shopify_updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(find_by_identification("1234567890"), newer)


class UpsertFromShopifyCustomerTests(TestCase):
    def test_creates_a_new_customer(self):
        upsert_from_shopify_customer(
            {
                "shopify_id": "1",
                "identification": "123",
                "email": "a@example.com",
                "phone": "",
                "first_name": "Ana",
                "last_name": "Gomez",
                "default_address_id": "gid://shopify/MailingAddress/1",
                "shopify_updated_at": "2026-09-15T00:00:00Z",
            }
        )
        customer = ShopifyCustomer.objects.get(shopify_id="1")
        self.assertEqual(customer.identification, "123")
        self.assertEqual(customer.default_address_id, "gid://shopify/MailingAddress/1")

    def test_updates_an_existing_customer(self):
        ShopifyCustomer.objects.create(shopify_id="1", identification="old")
        upsert_from_shopify_customer({"shopify_id": "1", "identification": "new", "default_address_id": ""})
        customer = ShopifyCustomer.objects.get(shopify_id="1")
        self.assertEqual(customer.identification, "new")

    def test_preserves_default_address_id_when_key_is_absent_from_webhook_data(self):
        ShopifyCustomer.objects.create(shopify_id="1", default_address_id="gid://shopify/MailingAddress/1")
        upsert_from_shopify_customer({"shopify_id": "1", "identification": "999"})
        customer = ShopifyCustomer.objects.get(shopify_id="1")
        self.assertEqual(customer.default_address_id, "gid://shopify/MailingAddress/1")
        self.assertEqual(customer.identification, "999")

    def test_overwrites_default_address_id_when_key_is_present_even_if_empty(self):
        ShopifyCustomer.objects.create(shopify_id="1", default_address_id="gid://shopify/MailingAddress/1")
        upsert_from_shopify_customer({"shopify_id": "1", "default_address_id": ""})
        customer = ShopifyCustomer.objects.get(shopify_id="1")
        self.assertEqual(customer.default_address_id, "")


class SetIdentificationTests(TestCase):
    @patch("customers.functions.set_identification.create_customer_address")
    def test_creates_the_first_address_when_none_exists(self, mock_create):
        mock_create.return_value = "gid://shopify/MailingAddress/1"
        customer = ShopifyCustomer.objects.create(shopify_id="1")

        result = set_identification(customer, "1234567890", first_name="Ana")

        mock_create.assert_called_once_with(
            customer_id="1", company="1234567890", first_name="Ana", last_name="", phone=""
        )
        self.assertEqual(result.default_address_id, "gid://shopify/MailingAddress/1")
        self.assertEqual(result.identification, "1234567890")
        customer.refresh_from_db()
        self.assertEqual(customer.identification, "1234567890")

    @patch("customers.functions.set_identification.update_customer_address")
    @patch("customers.functions.set_identification.create_customer_address")
    def test_updates_the_existing_address_instead_of_creating_a_new_one(self, mock_create, mock_update):
        customer = ShopifyCustomer.objects.create(
            shopify_id="1", default_address_id="gid://shopify/MailingAddress/1"
        )

        set_identification(customer, "1234567890")

        mock_create.assert_not_called()
        mock_update.assert_called_once_with(
            customer_id="1",
            address_id="gid://shopify/MailingAddress/1",
            company="1234567890",
            first_name="",
            last_name="",
            phone="",
        )


class ReconcileFromShopifyTests(TestCase):
    @patch("customers.functions.reconcile_from_shopify.list_customers_page")
    def test_pages_through_all_customers_and_upserts_each(self, mock_list):
        mock_list.side_effect = [
            {
                "customers": [{"shopify_id": "1", "identification": "", "default_address_id": ""}],
                "has_next_page": True,
                "end_cursor": "c1",
            },
            {
                "customers": [{"shopify_id": "2", "identification": "", "default_address_id": ""}],
                "has_next_page": False,
                "end_cursor": None,
            },
        ]

        reconcile_from_shopify()

        self.assertEqual(ShopifyCustomer.objects.count(), 2)
        self.assertEqual(mock_list.call_args_list[1].kwargs["cursor"], "c1")

    @patch("customers.functions.reconcile_from_shopify.list_customers_page")
    def test_reports_progress_up_to_100_at_the_end(self, mock_list):
        mock_list.return_value = {"customers": [], "has_next_page": False, "end_cursor": None}
        calls = []

        reconcile_from_shopify(progress_callback=lambda percent, step=None: calls.append(percent))

        self.assertEqual(calls[-1], 100)

    @patch("customers.functions.reconcile_from_shopify.list_customers_page")
    def test_checks_cancellation_after_each_page(self, mock_list):
        mock_list.side_effect = [
            {"customers": [], "has_next_page": True, "end_cursor": "c1"},
            {"customers": [], "has_next_page": False, "end_cursor": None},
        ]
        token = Mock()

        reconcile_from_shopify(cancellation_token=token)

        self.assertEqual(token.raise_if_cancelled.call_count, 2)


class ParseCustomerWebhookTests(SimpleTestCase):
    def test_normalizes_a_standard_shaped_payload(self):
        payload = {
            "id": 123456789,
            "email": "ana@example.com",
            "phone": "3001234567",
            "first_name": "Ana",
            "last_name": "Gomez",
            "updated_at": "2026-09-15T00:00:00-05:00",
            "default_address": {"id": 987654321, "company": "1234567890"},
        }
        result = parse_customer_webhook(payload)
        self.assertEqual(result["shopify_id"], "123456789")
        self.assertEqual(result["identification"], "1234567890")
        self.assertEqual(result["email"], "ana@example.com")
        self.assertNotIn("default_address_id", result)

    def test_handles_a_missing_default_address(self):
        payload = {"id": 1, "email": "", "phone": "", "first_name": "", "last_name": "", "updated_at": ""}
        result = parse_customer_webhook(payload)
        self.assertEqual(result["identification"], "")


class ProcessCustomerWebhookTests(TestCase):
    def test_parses_and_upserts_the_customer(self):
        payload = {
            "id": 1,
            "email": "ana@example.com",
            "first_name": "Ana",
            "last_name": "",
            "phone": "",
            "updated_at": "2026-09-15T00:00:00Z",
        }
        process_customer_webhook({"topic": "customers/create", "payload": payload})
        customer = ShopifyCustomer.objects.get(shopify_id="1")
        self.assertEqual(customer.email, "ana@example.com")

    def test_reports_progress_up_to_100(self):
        calls = []
        process_customer_webhook(
            {"topic": "customers/create", "payload": {"id": 1}},
            progress_callback=lambda percent, step=None: calls.append(percent),
        )
        self.assertEqual(calls[-1], 100)


class ShopifyCustomerWebhookViewTests(APITestCase):
    def _signed_body(self, payload):
        body = json.dumps(payload).encode()
        signature = base64.b64encode(
            hmac.new(SHOPIFY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
        ).decode()
        return body, signature

    @patch("orchestrator.core.runner.ThreadProcessRunner.start")
    def test_valid_signature_launches_the_process_and_responds_200(self, mock_thread_start):
        body, signature = self._signed_body({"id": 1, "email": "ana@example.com"})

        response = self.client.post(
            reverse("customers-shopify-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_TOPIC="customers/create",
        )

        self.assertEqual(response.status_code, 200)
        mock_thread_start.assert_called_once()
        self.assertTrue(
            ProcessExecution.objects.filter(process_type__code="customers.process_webhook").exists()
        )

    def test_invalid_signature_is_rejected_without_launching_anything(self):
        body = json.dumps({"id": 1}).encode()

        response = self.client.post(
            reverse("customers-shopify-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256="not-a-real-signature",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProcessExecution.objects.exists())

    def test_missing_signature_is_rejected(self):
        body = json.dumps({"id": 1}).encode()

        response = self.client.post(
            reverse("customers-shopify-webhook"), data=body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 403)
