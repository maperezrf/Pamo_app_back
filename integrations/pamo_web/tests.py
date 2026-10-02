from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .client import PamoWebAPIError
from .functions.run_sodimac_invoicing import run_sodimac_invoicing

BASE_URL_PATCH = "integrations.pamo_web.client.PAMO_WEB_BASE_URL"
TOKEN_PATCH = "integrations.pamo_web.client.PAMO_WEB_BOT_TOKEN"


@patch(BASE_URL_PATCH, "https://pamo-web.example/")
@patch(TOKEN_PATCH, "secret-token")
class RunSodimacInvoicingTests(SimpleTestCase):
    @patch("integrations.pamo_web.client.requests.post")
    def test_posts_with_the_bot_token_and_returns_the_summary(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"invoiced": 2}))

        self.assertEqual(run_sodimac_invoicing(), {"invoiced": 2})
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://pamo-web.example/pamo_bots/sodimac/invoices")
        self.assertEqual(kwargs["headers"]["X-Bot-Token"], "secret-token")
        self.assertFalse(kwargs["allow_redirects"])

    @patch("integrations.pamo_web.client.requests.post")
    def test_http_error_raises_a_code_without_the_token(self, mock_post):
        mock_post.return_value = Mock(status_code=403)

        with self.assertRaises(PamoWebAPIError) as raised:
            run_sodimac_invoicing()
        self.assertEqual(str(raised.exception), "PAMO_WEB_HTTP_403")

    @patch("integrations.pamo_web.client.requests.post")
    def test_a_redirect_is_an_error(self, mock_post):
        # create_orders de pamo_web siempre responde con redirect, aunque falle.
        mock_post.return_value = Mock(status_code=302)

        with self.assertRaises(PamoWebAPIError):
            run_sodimac_invoicing()

    @patch("integrations.pamo_web.client.requests.post")
    def test_non_json_body_is_an_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError))

        with self.assertRaises(PamoWebAPIError) as raised:
            run_sodimac_invoicing()
        self.assertEqual(str(raised.exception), "PAMO_WEB_RESPONSE_INVALID")

    @patch("integrations.pamo_web.client.requests.post")
    def test_fails_without_configuration_and_does_not_call(self, mock_post):
        # Dentro del test: un @patch del método lo pisaría el de la clase.
        with patch(TOKEN_PATCH, ""), self.assertRaises(PamoWebAPIError) as raised:
            run_sodimac_invoicing()
        self.assertEqual(str(raised.exception), "PAMO_WEB_NOT_CONFIGURED")
        mock_post.assert_not_called()
