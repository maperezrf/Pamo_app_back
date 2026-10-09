import base64
import email
from unittest.mock import MagicMock, patch

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from .client import GmailAPIError, GmailClient

C = "integrations.gmail.client"
BACKEND = "integrations.gmail.email_backend.GmailApiEmailBackend"


def _response(status, body):
    response = MagicMock(status_code=status)
    response.json.return_value = body
    return response


@override_settings(EMAIL_BACKEND=BACKEND)
@patch(f"{C}.GMAIL_REFRESH_TOKEN", "refresh")
@patch(f"{C}.GMAIL_CLIENT_SECRET", "secret")
@patch(f"{C}.GMAIL_CLIENT_ID", "client")
class GmailApiEmailBackendTests(SimpleTestCase):
    def setUp(self):
        GmailClient._access_token = ""
        GmailClient._expires_at = 0.0

    @patch(f"{C}.requests.post")
    def test_sends_the_message_with_its_attachment_as_raw_mime(self, post):
        post.side_effect = [_response(200, {"access_token": "tok", "expires_in": 3599}), _response(200, {"id": "m1"})]
        message = EmailMessage("Despacho 20500", "Cuerpo", "notificaciones@example.com", ["bodega@example.com"])
        message.attach("guia-20500.pdf", b"%PDF-1", "application/pdf")

        self.assertEqual(message.send(), 1)

        token_call, send_call = post.call_args_list
        self.assertEqual(token_call.kwargs["data"]["refresh_token"], "refresh")
        self.assertEqual(send_call.args[0], "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        self.assertEqual(send_call.kwargs["headers"], {"Authorization": "Bearer tok"})
        sent = email.message_from_bytes(base64.urlsafe_b64decode(send_call.kwargs["json"]["raw"]))
        self.assertEqual(sent["To"], "bodega@example.com")
        self.assertEqual([part.get_filename() for part in sent.walk() if part.get_filename()], ["guia-20500.pdf"])

    @patch(f"{C}.requests.post")
    def test_the_access_token_is_reused_while_valid(self, post):
        post.side_effect = [
            _response(200, {"access_token": "tok", "expires_in": 3599}),
            _response(200, {"id": "m1"}),
            _response(200, {"id": "m2"}),
        ]
        for _ in range(2):
            EmailMessage("s", "b", "a@example.com", ["b@example.com"]).send()

        self.assertEqual(post.call_count, 3)  # un solo pedido de token

    @patch(f"{C}.requests.post", return_value=_response(400, {"error": "invalid_grant"}))
    def test_a_revoked_refresh_token_raises_with_the_reason(self, post):
        with self.assertRaisesMessage(GmailAPIError, "invalid_grant"):
            EmailMessage("s", "b", "a@example.com", ["b@example.com"]).send()

    @patch(f"{C}.requests.post", return_value=_response(400, {"error": "invalid_grant"}))
    def test_fail_silently_counts_nothing_sent(self, post):
        message = EmailMessage("s", "b", "a@example.com", ["b@example.com"])

        self.assertEqual(message.send(fail_silently=True), 0)
