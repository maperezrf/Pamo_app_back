from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .client import SiigoAPIError, SiigoClient
from .functions.create_invoice import SiigoInvoiceError, create_invoice
from .functions.get_customer import get_customer
from .functions.search_customer_by_identification import search_customer_by_identification
from .models import SiigoToken


def _valid_cached_token(value="cached-token"):
    return SiigoToken.objects.create(
        id=1, token=value, expires_at=timezone.now() + timedelta(hours=1)
    )


class TokenCachingTests(TestCase):
    @patch("integrations.siigo.client.requests.post")
    def test_authenticates_and_caches_the_token_when_none_exists(self, mock_post):
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"access_token": "brand-new-token"}
        token = SiigoClient()._valid_token()
        self.assertEqual(token, "brand-new-token")
        self.assertEqual(SiigoToken.objects.get(id=1).token, "brand-new-token")

    @patch("integrations.siigo.client.requests.post")
    def test_reuses_a_valid_cached_token_without_calling_the_api(self, mock_post):
        _valid_cached_token("still-valid")
        token = SiigoClient()._valid_token()
        self.assertEqual(token, "still-valid")
        mock_post.assert_not_called()

    @patch("integrations.siigo.client.requests.post")
    def test_refreshes_an_expired_cached_token(self, mock_post):
        SiigoToken.objects.create(
            id=1, token="expired", expires_at=timezone.now() - timedelta(minutes=1)
        )
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"access_token": "refreshed-token"}
        token = SiigoClient()._valid_token()
        self.assertEqual(token, "refreshed-token")
        mock_post.assert_called_once()


class RequestTests(TestCase):
    @patch("integrations.siigo.client.requests.request")
    def test_sends_partner_id_and_authorization_headers(self, mock_request):
        _valid_cached_token("the-token")
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {}
        SiigoClient().request("GET", "customers/1")
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "the-token")
        self.assertIn("Partner-Id", kwargs["headers"])

    @patch("integrations.siigo.client.requests.request")
    def test_raises_siigo_api_error_with_the_response_body_on_a_rejection(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = False
        mock_request.return_value.status_code = 400
        mock_request.return_value.json.return_value = {"Errors": [{"Message": "Invalid retention id"}]}
        with self.assertRaises(SiigoAPIError) as ctx:
            SiigoClient().request("POST", "invoices", json_body={})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid retention id", str(ctx.exception.body))


def _raw_customer(customer_id="cust-1", identification="123", check_digit="4"):
    return {
        "id": customer_id,
        "id_type": {"code": "13", "name": "Cédula de ciudadanía"},
        "identification": identification,
        "check_digit": check_digit,
        "name": ["Carlos", "Gomez"],
        "contacts": [{"email": "carlos@example.com"}],
    }


class GetCustomerTests(TestCase):
    @patch("integrations.siigo.client.requests.request")
    def test_normalizes_the_customer(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = _raw_customer()
        result = get_customer("cust-1")
        self.assertEqual(
            result,
            {
                "id": "cust-1",
                "document_type": "Cédula de ciudadanía",
                "identification": "123",
                "check_digit": "4",
                "name": "Carlos Gomez",
                "email": "carlos@example.com",
            },
        )


class SearchCustomerByIdentificationTests(TestCase):
    @patch("integrations.siigo.client.requests.request")
    def test_returns_the_customer_when_found(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"results": [_raw_customer()]}
        result = search_customer_by_identification("123")
        self.assertEqual(result["identification"], "123")

    @patch("integrations.siigo.client.requests.request")
    def test_returns_none_when_no_results(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"results": []}
        self.assertIsNone(search_customer_by_identification("000"))

    @patch("integrations.siigo.client.requests.request")
    def test_sends_the_identification_as_a_query_parameter(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"results": []}
        search_customer_by_identification("900123456")
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["identification"], "900123456")


class CreateInvoiceTests(TestCase):
    def _kwargs(self, **overrides):
        base = dict(
            document_id=1,
            customer={"identification": "900123456"},
            items=[{"code": "SKU-1", "quantity": 1, "price": 1000, "discount": 0, "taxes": []}],
            cost_center=1,
            seller=1,
            retention_ids=[111, 222],
            reteica=10,
            payment_id=1,
            payment_value=1190,
            stamp_send=False,
        )
        base.update(overrides)
        return base

    @patch("integrations.siigo.client.requests.request")
    def test_returns_the_invoice_id_on_success(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"id": "inv-1", "name": "FV-1"}
        result = create_invoice(**self._kwargs())
        self.assertEqual(result["invoice_id"], "inv-1")

    @patch("integrations.siigo.client.requests.request")
    def test_raises_when_the_response_has_no_invoice_id(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"some": "thing"}
        with self.assertRaises(SiigoInvoiceError):
            create_invoice(**self._kwargs())

    @patch("integrations.siigo.client.requests.request")
    def test_stamp_send_is_never_defaulted_to_true(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"id": "inv-1"}
        create_invoice(**self._kwargs(stamp_send=False))
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["stamp"], {"send": False})

    @patch("integrations.siigo.client.requests.request")
    def test_builds_retentions_from_retention_ids(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"id": "inv-1"}
        create_invoice(**self._kwargs(retention_ids=[111, 222]))
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["retentions"], [{"id": 111}, {"id": 222}])

    @patch("integrations.siigo.client.requests.request")
    def test_includes_purchase_order_only_when_given(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = True
        mock_request.return_value.json.return_value = {"id": "inv-1"}

        create_invoice(**self._kwargs())
        self.assertNotIn("additional_fields", mock_request.call_args.kwargs["json"])

        create_invoice(**self._kwargs(purchase_order_number="OC-1"))
        self.assertEqual(
            mock_request.call_args.kwargs["json"]["additional_fields"],
            {"purchase_order": {"number": "OC-1"}},
        )

    @patch("integrations.siigo.client.requests.request")
    def test_propagates_siigo_api_error_when_the_provider_rejects_it(self, mock_request):
        _valid_cached_token()
        mock_request.return_value.ok = False
        mock_request.return_value.status_code = 400
        mock_request.return_value.json.return_value = {"Errors": [{"Message": "Invalid cost center"}]}
        with self.assertRaises(SiigoAPIError):
            create_invoice(**self._kwargs())
