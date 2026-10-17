from unittest.mock import Mock, patch

from django.test import TestCase

from .client import ZernioAPIError, ZernioClient
from .functions.list_whatsapp_templates import list_whatsapp_templates
from .functions.send_whatsapp_template import SendWhatsAppTemplateError, send_whatsapp_template
from .functions.upload_media import UploadMediaError, upload_media


def _mock_response(status_code=200, json_body=None):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_body or {}
    return response


@patch("integrations.zernio.client.ZERNIO_API_KEY", "key-1")
class ZernioClientTests(TestCase):
    @patch("integrations.zernio.client.requests.post")
    def test_post_json_raises_on_http_error(self, mock_post):
        mock_post.return_value = _mock_response(400, {"error": "x", "code": "INVALID_TEMPLATE_PARAMS"})
        with self.assertRaises(ZernioAPIError) as raised:
            ZernioClient().post_json("inbox/conversations", {})
        self.assertEqual(raised.exception.body["code"], "INVALID_TEMPLATE_PARAMS")

    @patch("integrations.zernio.client.requests.get")
    def test_sends_the_api_key_as_bearer(self, mock_get):
        mock_get.return_value = _mock_response(200, {})
        ZernioClient().get("whatsapp/templates")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer key-1")


@patch("integrations.zernio.functions.send_whatsapp_template.ZERNIO_WHATSAPP_ACCOUNT_ID", "acc-1")
class SendWhatsAppTemplateTests(TestCase):
    def test_rejects_invalid_template_name(self):
        with self.assertRaises(SendWhatsAppTemplateError):
            send_whatsapp_template("573001234567", "Not Valid!")

    @patch("integrations.zernio.client.requests.post")
    def test_happy_path_with_body_params_and_document(self, mock_post):
        mock_post.return_value = _mock_response(201, {"success": True, "data": {"messageId": "wamid.3"}})
        result = send_whatsapp_template(
            "573001234567",
            "despacho_bodega",
            body_params=["#1001", 2],
            document_url="https://media.zernio.com/temp/x.pdf",
            document_filename="guia-1001.pdf",
        )
        self.assertEqual(result["message_id"], "wamid.3")
        self.assertTrue(mock_post.call_args.args[0].endswith("/inbox/conversations"))
        self.assertEqual(mock_post.call_args.kwargs["json"], {
            "accountId": "acc-1",
            "participantId": "573001234567",
            "templateName": "despacho_bodega",
            "templateLanguage": "es_CO",
            "templateParams": ["#1001", "2"],
            "headerMedia": {
                "type": "document", "link": "https://media.zernio.com/temp/x.pdf", "filename": "guia-1001.pdf",
            },
        })

    @patch("integrations.zernio.client.requests.post")
    def test_without_document_there_is_no_header(self, mock_post):
        mock_post.return_value = _mock_response(201, {"success": True, "data": {"messageId": "wamid.4"}})
        send_whatsapp_template("573001234567", "hello_world", language="en_US")
        self.assertNotIn("headerMedia", mock_post.call_args.kwargs["json"])


class UploadMediaTests(TestCase):
    def test_rejects_empty_content(self):
        with self.assertRaises(UploadMediaError):
            upload_media(b"", "guia.pdf")

    @patch("integrations.zernio.client.requests.post")
    def test_rejects_missing_url_in_response(self, mock_post):
        mock_post.return_value = _mock_response(200, {})
        with self.assertRaises(UploadMediaError):
            upload_media(b"%PDF", "guia.pdf")

    @patch("integrations.zernio.client.requests.post")
    def test_happy_path(self, mock_post):
        mock_post.return_value = _mock_response(200, {"url": "https://media.zernio.com/temp/x.pdf"})
        self.assertEqual(upload_media(b"%PDF", "guia.pdf"), "https://media.zernio.com/temp/x.pdf")
        self.assertTrue(mock_post.call_args.args[0].endswith("/media/upload-direct"))
        self.assertEqual(mock_post.call_args.kwargs["files"]["file"], ("guia.pdf", b"%PDF", "application/pdf"))


@patch("integrations.zernio.functions.list_whatsapp_templates.ZERNIO_WHATSAPP_ACCOUNT_ID", "acc-1")
class ListWhatsAppTemplatesTests(TestCase):
    @patch("integrations.zernio.client.requests.get")
    def test_normalizes_templates(self, mock_get):
        mock_get.return_value = _mock_response(200, {"success": True, "templates": [{
            "name": "despacho_bodega", "language": "es_CO", "status": "APPROVED", "category": "UTILITY",
            "components": [{"type": "HEADER", "format": "DOCUMENT"}, {"type": "BODY", "text": "Pedido {{1}}: {{2}} a {{3}}"}],
        }]})
        self.assertEqual(list_whatsapp_templates(), [{
            "name": "despacho_bodega", "language": "es_CO", "status": "APPROVED", "category": "UTILITY",
            "body": "Pedido {{1}}: {{2}} a {{3}}", "body_variables": 3, "header_format": "DOCUMENT",
        }])
        self.assertEqual(mock_get.call_args.kwargs["params"], {"accountId": "acc-1"})
