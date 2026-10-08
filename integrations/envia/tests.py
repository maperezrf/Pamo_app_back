from unittest.mock import MagicMock, patch

import requests

from django.test import SimpleTestCase

from .client import EnviaAPIError
from .functions.create_label import LabelUnknownResult, LabelWritesDisabled, create_label
from .functions.download_label import download_label
from .functions.quote import QuoteError, quote
from .functions.resolve_colombia_city import resolve_colombia_city
from .functions.validate_shipping_payload import ShippingPayloadError, validate_shipping_payload


def _valid_payload(**overrides):
    payload = {
        "origin": {
            "name": "Bodega", "phone": "+573001112233", "street": "Calle 1",
            "city": "11001001", "state": "11", "country": "CO", "postalCode": "110111",
        },
        "destination": {
            "name": "Cliente", "phone": "+573004445566", "street": "Calle 2",
            "city": "76001001", "state": "76", "country": "CO", "postalCode": "760001",
        },
        "packages": [
            {"content": "Producto", "amount": 1, "weight": 1.5, "dimensions": {"length": 10, "width": 10, "height": 10}},
        ],
    }
    payload.update(overrides)
    return payload


class ValidateShippingPayloadTests(SimpleTestCase):
    def test_accepts_a_well_formed_payload(self):
        validated = validate_shipping_payload(_valid_payload())
        self.assertEqual(validated["origin"]["city"], "11001001")
        self.assertEqual(validated["packages"][0]["weightUnit"], "KG")

    def test_rejects_a_colombian_city_that_is_not_an_8_digit_dane_code(self):
        payload = _valid_payload()
        payload["destination"]["city"] = "Cali"
        with self.assertRaises(ShippingPayloadError):
            validate_shipping_payload(payload)

    def test_rejects_a_city_that_looks_like_a_dane_code_but_is_the_wrong_length(self):
        payload = _valid_payload()
        payload["destination"]["city"] = "1234567"  # 7 dígitos, no 8
        with self.assertRaises(ShippingPayloadError):
            validate_shipping_payload(payload)

    def test_rejects_a_malformed_phone(self):
        payload = _valid_payload()
        payload["origin"]["phone"] = "123"
        with self.assertRaises(ShippingPayloadError):
            validate_shipping_payload(payload)

    def test_rejects_more_than_20_packages(self):
        payload = _valid_payload(packages=[
            {"content": "x", "amount": 1, "weight": 1, "dimensions": {"length": 1, "width": 1, "height": 1}}
            for _ in range(21)
        ])
        with self.assertRaises(ShippingPayloadError):
            validate_shipping_payload(payload)


class ResolveColombiaCityTests(SimpleTestCase):
    @patch("integrations.envia.client.requests.post")
    @patch("integrations.envia.client.requests.get")
    def test_resolves_a_matching_city(self, mock_get, mock_post):
        mock_get.return_value.is_redirect = False
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = (
            b'{"data": [{"country_code": "CO", "code_shopify": "VAC", "code_2_digits": "76"}]}'
        )
        mock_post.return_value.is_redirect = False
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = b'{"city": "76001001", "state": "76", "name": "Cali"}'

        result = resolve_colombia_city("Cali", "VAC")
        self.assertEqual(result, {"city": "76001001", "state": "76"})

    @patch("integrations.envia.client.requests.post")
    @patch("integrations.envia.client.requests.get")
    def test_raises_when_the_returned_city_name_does_not_match(self, mock_get, mock_post):
        mock_get.return_value.is_redirect = False
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = (
            b'{"data": [{"country_code": "CO", "code_shopify": "VAC", "code_2_digits": "76"}]}'
        )
        mock_post.return_value.is_redirect = False
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = b'{"city": "76001001", "state": "76", "name": "Otra ciudad"}'

        with self.assertRaises(EnviaAPIError):
            resolve_colombia_city("Cali", "VAC")

    @patch("integrations.envia.client.requests.get")
    def test_raises_when_the_shopify_state_has_no_unique_match(self, mock_get):
        mock_get.return_value.is_redirect = False
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b'{"data": []}'
        with self.assertRaises(EnviaAPIError):
            resolve_colombia_city("Cali", "VAC")


class QuoteTests(SimpleTestCase):
    @patch("integrations.envia.functions.quote.ENVIA_ALLOWED_CARRIERS", ["carrier-a"])
    @patch("integrations.envia.client.requests.post")
    def test_returns_normalized_options(self, mock_post):
        mock_post.return_value.is_redirect = False
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = (
            b'{"data": [{"carrier": "carrier-a", "service": "Standard", '
            b'"totalPrice": "15000", "currency": "COP", "deliveryEstimate": "2-3 days"}]}'
        )
        options = quote(_valid_payload())
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["carrier"], "carrier-a")
        self.assertEqual(options[0]["price"], 15000.0)
        self.assertEqual(options[0]["etaMinDays"], 2)
        self.assertEqual(options[0]["etaMaxDays"], 3)
        self.assertIn("providerPayload", options[0])

    @patch("integrations.envia.functions.quote.ENVIA_ALLOWED_CARRIERS", ["carrier-a"])
    @patch("integrations.envia.client.requests.post")
    def test_a_non_json_auth_error_reports_the_http_status(self, mock_post):
        # Visto en producción (2026-10-08): token inválido -> 401 en texto.
        mock_post.return_value.is_redirect = False
        mock_post.return_value.status_code = 401
        mock_post.return_value.content = b"Authentication error."
        with self.assertRaises(EnviaAPIError) as raised:
            quote(_valid_payload())
        self.assertEqual(raised.exception.code, "ENVIA_HTTP_401")

    @patch("integrations.envia.functions.quote.ENVIA_ALLOWED_CARRIERS", [])
    def test_raises_when_no_carriers_are_configured(self):
        with self.assertRaises(QuoteError):
            quote(_valid_payload())

    @patch("integrations.envia.functions.quote.ENVIA_ALLOWED_CARRIERS", ["carrier-a"])
    @patch("integrations.envia.client.requests.post")
    def test_raises_when_no_carrier_returns_an_eligible_rate(self, mock_post):
        mock_post.return_value.is_redirect = False
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = b'{"data": []}'
        with self.assertRaises(QuoteError):
            quote(_valid_payload())


LABEL = "integrations.envia.functions.create_label"
OPTION = {"carrier": "tcc", "service": "mensajeria", "providerPayload": {"carrier": "tcc", "service": "mensajeria", "type": 1}}


def _generate_response(content, status=200):
    response = MagicMock()
    response.is_redirect = False
    response.status_code = status
    response.content = content
    return response


@patch(f"{LABEL}.ENVIA_ALLOWED_CARRIERS", ["tcc", "servientrega"])
class CreateLabelTests(SimpleTestCase):
    @patch(f"{LABEL}.ENVIA_WRITES_ENABLED", False)
    @patch("integrations.envia.client.requests.post")
    def test_is_blocked_without_writes_enabled(self, mock_post):
        with self.assertRaises(LabelWritesDisabled):
            create_label(_valid_payload(), OPTION, order_reference="shopify-20412")
        mock_post.assert_not_called()

    @patch(f"{LABEL}.ENVIA_WRITES_ENABLED", True)
    @patch("integrations.envia.client.requests.post")
    def test_validates_before_sending(self, mock_post):
        with self.assertRaises(ValueError):
            create_label(_valid_payload(), OPTION, order_reference="con espacios")
        with self.assertRaises(ValueError):
            create_label(_valid_payload(), {"providerPayload": {"carrier": "fedex", "service": "x"}}, order_reference="a")
        with self.assertRaises(ValueError):
            create_label(_valid_payload(), {"providerPayload": {"carrier": "tcc"}}, order_reference="a")
        mock_post.assert_not_called()

    @patch(f"{LABEL}.ENVIA_WRITES_ENABLED", True)
    @patch("integrations.envia.client.requests.post")
    def test_generates_the_selected_option_and_returns_tracking_and_label(self, mock_post):
        mock_post.return_value = _generate_response(
            b'{"meta": "generate", "data": [{"carrier": "tcc", "service": "mensajeria", '
            b'"trackingNumber": "474231399", "label": "https://s3.us-east-2.amazonaws.com/enviapaqueteria/uploads/tcc/abc123.pdf", '
            b'"shipmentId": 9988}]}'
        )

        result = create_label(_valid_payload(), OPTION, order_reference="shopify-20412")

        self.assertEqual(result["tracking_number"], "474231399")
        self.assertEqual(result["shipment_id"], "9988")
        self.assertTrue(mock_post.call_args.args[0].endswith("/ship/generate/"))
        import json
        sent = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(sent["shipment"], {"type": 1, "carrier": "tcc", "service": "mensajeria", "orderReference": "shopify-20412"})

    @patch(f"{LABEL}.ENVIA_WRITES_ENABLED", True)
    @patch("integrations.envia.client.requests.post")
    def test_any_failure_after_sending_is_an_unknown_result(self, mock_post):
        cases = [
            requests.Timeout("lento"),
            _generate_response(b'{"meta": "error", "error": {"message": "x"}}', status=500),
            _generate_response(b"Authentication error.", status=401),
            _generate_response(b'{"data": [{"trackingNumber": "1"}]}'),  # sin label
            _generate_response(b'{"data": [{"trackingNumber": "1", "label": "a"}, {"trackingNumber": "2", "label": "b"}]}'),
        ]
        for case in cases:
            if isinstance(case, Exception):
                mock_post.side_effect, mock_post.return_value = case, None
            else:
                mock_post.side_effect, mock_post.return_value = None, case
            with self.assertRaises(LabelUnknownResult):
                create_label(_valid_payload(), OPTION, order_reference="shopify-20412")


DOWNLOAD = "integrations.envia.functions.download_label"
PDF = b"%PDF-1.4 " + b"x" * 64 + b" %%EOF"


def _pdf_response(content=PDF, status=200):
    response = MagicMock()
    response.is_redirect = False
    response.status_code = status
    response.iter_content.return_value = [content]
    return response


@patch(f"{DOWNLOAD}.ENVIA_API_TOKEN", "token-secreto")
@patch(f"{DOWNLOAD}.ENVIA_ALLOWED_CARRIERS", ["tcc"])
class DownloadLabelTests(SimpleTestCase):
    @patch(f"{DOWNLOAD}.requests.get")
    def test_storage_download_never_sends_the_token(self, mock_get):
        mock_get.return_value = _pdf_response()
        self.assertEqual(download_label("https://s3.us-east-2.amazonaws.com/enviapaqueteria/uploads/tcc/abc123.pdf"), PDF)
        self.assertNotIn("Authorization", mock_get.call_args.kwargs["headers"])

    @patch(f"{DOWNLOAD}.requests.get")
    def test_envia_host_download_sends_the_token(self, mock_get):
        mock_get.return_value = _pdf_response()
        download_label("https://api.envia.com/labels/abc.pdf")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer token-secreto")

    @patch(f"{DOWNLOAD}.requests.get")
    def test_rejects_untrusted_urls_before_downloading(self, mock_get):
        for url in (
            "http://api.envia.com/a.pdf",
            "https://evil.com/a.pdf",
            "https://api.envia.com.evil.com/a.pdf",
            "https://s3.us-east-2.amazonaws.com/enviapaqueteria/uploads/fedex/a.pdf",
            "https://s3.us-east-2.amazonaws.com/otro/bucket/a.pdf",
        ):
            with self.assertRaises(EnviaAPIError):
                download_label(url)
        mock_get.assert_not_called()

    @patch(f"{DOWNLOAD}.requests.get")
    def test_rejects_a_response_that_is_not_a_pdf(self, mock_get):
        mock_get.return_value = _pdf_response(content=b"<html>login</html>" * 3)
        with self.assertRaises(EnviaAPIError):
            download_label("https://api.envia.com/labels/abc.pdf")
