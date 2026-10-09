import base64

from ..client import GmailClient


def send_message(email_message):
    """Envía un `django.core.mail.EmailMessage` (con adjuntos) tal cual,
    como MIME crudo. Gmail pone como remitente la cuenta autorizada.
    Devuelve el id del mensaje en Gmail."""
    raw = base64.urlsafe_b64encode(email_message.message().as_bytes()).decode()
    return GmailClient().post("messages/send", {"raw": raw})["id"]
