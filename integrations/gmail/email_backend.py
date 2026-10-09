from django.core.mail.backends.base import BaseEmailBackend

from .functions.send_message import send_message


class GmailApiEmailBackend(BaseEmailBackend):
    """Backend de `django.core.mail` que envía por la API de Gmail (HTTPS)
    en vez de SMTP. Se activa con GMAIL_REFRESH_TOKEN (ver EMAIL_BACKEND
    en config/constants.py); quien envía sigue usando `EmailMessage`."""

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                send_message(message)
            except Exception:
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent
