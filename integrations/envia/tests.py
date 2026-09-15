from unittest.mock import patch

from django.test import SimpleTestCase

from .client import EnviaAPIError
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
