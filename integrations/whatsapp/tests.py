from unittest.mock import Mock, patch

from django.test import TestCase

from .client import WhatsAppAPIError, WhatsAppClient
from .functions.parse_webhook_event import WhatsAppWebhookError, parse_webhook_event
from .functions.send_button_message import SendButtonMessageError, send_button_message
from .functions.send_document_message import SendDocumentMessageError, send_document_message
from .functions.send_template_message import SendTemplateMessageError, send_template_message
from .functions.send_text_message import send_text_message
from .functions.upload_document import UploadDocumentError, upload_document


def _mock_response(status_code=200, json_body=None):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_body or {}
    return response


class WhatsAppClientTests(TestCase):
    @patch("integrations.whatsapp.client.requests.post")
    def test_send_message_raises_on_http_error(self, mock_post):
        mock_post.return_value = _mock_response(400, {"error": {"code": 131026, "message": "x"}})
        with self.assertRaises(WhatsAppAPIError):
            WhatsAppClient().send_message({"type": "text"})

    def test_verify_signature_rejects_missing_prefix(self):
        self.assertFalse(WhatsAppClient.verify_signature(b"body", "not-sha256"))

    def test_verify_signature_rejects_wrong_digest(self):
        self.assertFalse(WhatsAppClient.verify_signature(b"body", "sha256=" + "0" * 64))


class SendTextMessageTests(TestCase):
    @patch("integrations.whatsapp.client.requests.post")
    def test_happy_path(self, mock_post):
        mock_post.return_value = _mock_response(200, {"messages": [{"id": "wamid.1"}]})
        result = send_text_message("573001234567", "hola")
        self.assertEqual(result["message_id"], "wamid.1")
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["type"], "text")
        self.assertEqual(sent_payload["text"]["body"], "hola")


class SendDocumentMessageTests(TestCase):
    def test_rejects_when_neither_media_id_nor_link(self):
        with self.assertRaises(SendDocumentMessageError):
            send_document_message("573001234567")

    def test_rejects_when_both_media_id_and_link(self):
        with self.assertRaises(SendDocumentMessageError):
            send_document_message("573001234567", media_id="123", link="https://x.test/a.pdf")

    @patch("integrations.whatsapp.client.requests.post")
    def test_happy_path_with_link(self, mock_post):
        mock_post.return_value = _mock_response(200, {"messages": [{"id": "wamid.2"}]})
        result = send_document_message("573001234567", link="https://x.test/a.pdf", caption="guía")
        self.assertEqual(result["message_id"], "wamid.2")
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["document"]["link"], "https://x.test/a.pdf")
        self.assertEqual(sent_payload["document"]["caption"], "guía")


class UploadDocumentTests(TestCase):
    def test_rejects_empty_content(self):
        with self.assertRaises(UploadDocumentError):
            upload_document(b"", "a.pdf")

    @patch("integrations.whatsapp.client.requests.post")
    def test_rejects_missing_media_id_in_response(self, mock_post):
        mock_post.return_value = _mock_response(200, {})
        with self.assertRaises(UploadDocumentError):
            upload_document(b"%PDF-1.4", "a.pdf")

    @patch("integrations.whatsapp.client.requests.post")
    def test_happy_path(self, mock_post):
        mock_post.return_value = _mock_response(200, {"id": "media-1"})
        self.assertEqual(upload_document(b"%PDF-1.4", "a.pdf"), "media-1")


class SendTemplateMessageTests(TestCase):
    def test_rejects_invalid_template_name(self):
        with self.assertRaises(SendTemplateMessageError):
            send_template_message("573001234567", "Not Valid!")

    @patch("integrations.whatsapp.client.requests.post")
    def test_happy_path_with_body_params_and_document(self, mock_post):
        mock_post.return_value = _mock_response(200, {"messages": [{"id": "wamid.3"}]})
        result = send_template_message(
            "573001234567",
            "pamo_proveedor_solicitud_pedido_v1",
            body_params=["ACME", "DESP-1"],
            document_media_id="media-1",
        )
        self.assertEqual(result["message_id"], "wamid.3")
        components = mock_post.call_args.kwargs["json"]["template"]["components"]
        self.assertEqual(components[0]["type"], "header")
        self.assertEqual(components[1]["parameters"][0]["text"], "ACME")


class SendButtonMessageTests(TestCase):
    def test_rejects_no_buttons(self):
        with self.assertRaises(SendButtonMessageError):
            send_button_message("573001234567", "texto", [])

    def test_rejects_too_many_buttons(self):
        buttons = [{"id": str(i), "title": str(i)} for i in range(4)]
        with self.assertRaises(SendButtonMessageError):
            send_button_message("573001234567", "texto", buttons)

    @patch("integrations.whatsapp.client.requests.post")
    def test_happy_path(self, mock_post):
        mock_post.return_value = _mock_response(200, {"messages": [{"id": "wamid.4"}]})
        result = send_button_message(
            "573001234567", "¿Confirmas?", [{"id": "A", "title": "Aceptar"}]
        )
        self.assertEqual(result["message_id"], "wamid.4")
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["interactive"]["type"], "button")


class ParseWebhookEventTests(TestCase):
    def test_rejects_wrong_object(self):
        with self.assertRaises(WhatsAppWebhookError):
            parse_webhook_event({"object": "page"})

    def test_rejects_waba_mismatch(self):
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "wrong-waba", "changes": []}]}
        with self.assertRaises(WhatsAppWebhookError):
            parse_webhook_event(payload)

    @patch("integrations.whatsapp.functions.parse_webhook_event.WHATSAPP_BUSINESS_ACCOUNT_ID", "waba-1")
    @patch("integrations.whatsapp.functions.parse_webhook_event.WHATSAPP_PHONE_NUMBER_ID", "phone-1")
    def test_parses_status_and_inbound_events(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "waba-1",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "phone-1"},
                        "statuses": [{"id": "wamid.1", "status": "delivered", "timestamp": "1"}],
                        "messages": [{
                            "id": "wamid.2",
                            "from": "573001234567",
                            "timestamp": "2",
                            "button": {"payload": "ORDER_RECEIVED"},
                            "context": {"id": "wamid.0"},
                        }],
                    },
                }],
            }],
        }
        events = parse_webhook_event(payload)
        self.assertEqual(events[0], {
            "type": "status", "message_id": "wamid.1", "status": "DELIVERED", "timestamp": "1",
        })
        self.assertEqual(events[1]["type"], "inbound")
        self.assertEqual(events[1]["button_payload"], "ORDER_RECEIVED")
        self.assertEqual(events[1]["context_message_id"], "wamid.0")
