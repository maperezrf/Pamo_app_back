import hashlib
from abc import ABC, abstractmethod

import requests
from django.conf import settings


class WhatsAppProviderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ExternalWritesDisabled(WhatsAppProviderError):
    pass


def normalized_phone(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def meta_external_writes_ready(recipient_phone=""):
    required = {
        "META_APP_SECRET": settings.META_APP_SECRET,
        "META_WABA_ID": settings.META_WABA_ID,
        "META_PHONE_NUMBER_ID": settings.META_PHONE_NUMBER_ID,
        "META_SYSTEM_USER_TOKEN": settings.META_SYSTEM_USER_TOKEN,
        "META_GRAPH_API_VERSION": settings.META_GRAPH_API_VERSION,
    }
    missing = [name for name, value in required.items() if not value]
    gates = (
        settings.EXTERNAL_WRITES_ENABLED
        and settings.MESSAGING_EXTERNAL_WRITES_ENABLED
        and settings.PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED
    )
    allowlist = {normalized_phone(item) for item in settings.META_RECIPIENT_ALLOWLIST if item}
    allowed_recipient = not recipient_phone or normalized_phone(recipient_phone) in allowlist
    return {
        "ready": bool(gates and not missing and allowlist and allowed_recipient),
        "gatesEnabled": bool(gates),
        "missingConfiguration": missing,
        "allowlistConfigured": bool(allowlist),
        "recipientAllowed": bool(allowed_recipient),
    }


class WhatsAppProviderClient(ABC):
    name = "base"

    @abstractmethod
    def upload_media(self, *, file_object, mime_type, filename):
        raise NotImplementedError

    @abstractmethod
    def send_message(self, *, recipient_phone, body, media_id="", filename=""):
        raise NotImplementedError


class MockWhatsAppClient(WhatsAppProviderClient):
    name = "mock"

    def upload_media(self, *, file_object, mime_type, filename):
        content = file_object.read()
        if mime_type == "application/pdf" and not content.startswith(b"%PDF"):
            raise WhatsAppProviderError("INVALID_PDF", "El archivo no es un PDF válido.")
        digest = hashlib.sha256(content).hexdigest()
        return {"id": f"mock-media-{digest[:24]}", "sha256": digest}

    def send_message(self, *, recipient_phone, body, media_id="", filename=""):
        fingerprint = hashlib.sha256(
            f"{normalized_phone(recipient_phone)}|{hashlib.sha256(body.encode()).hexdigest()}|{media_id}".encode()
        ).hexdigest()
        return {"id": f"mock-message-{fingerprint[:28]}"}


class MetaWhatsAppClient(WhatsAppProviderClient):
    name = "meta"

    def __init__(self, *, session=None):
        self.session = session or requests.Session()
        self.base_url = (
            f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/"
            f"{settings.META_PHONE_NUMBER_ID}"
        )

    def _headers(self):
        return {"Authorization": f"Bearer {settings.META_SYSTEM_USER_TOKEN}"}

    def upload_media(self, *, file_object, mime_type, filename):
        response = self.session.post(
            f"{self.base_url}/media",
            headers=self._headers(),
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, file_object, mime_type)},
            timeout=20,
        )
        if response.status_code >= 400:
            raise WhatsAppProviderError(
                f"META_MEDIA_HTTP_{response.status_code}",
                "Meta rechazó la carga del documento.",
            )
        media_id = str(response.json().get("id", ""))
        if not media_id:
            raise WhatsAppProviderError("META_MEDIA_ID_MISSING", "Meta no devolvió media_id.")
        return {"id": media_id}

    def send_message(self, *, recipient_phone, body, media_id="", filename=""):
        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone(recipient_phone),
        }
        if media_id:
            payload.update(
                {
                    "type": "document",
                    "document": {"id": media_id, "caption": body, "filename": filename},
                }
            )
        else:
            payload.update({"type": "text", "text": {"preview_url": False, "body": body}})
        response = self.session.post(
            f"{self.base_url}/messages",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            raise WhatsAppProviderError(
                f"META_MESSAGE_HTTP_{response.status_code}",
                "Meta rechazó el mensaje.",
            )
        messages = response.json().get("messages") or []
        message_id = str(messages[0].get("id", "")) if messages else ""
        if not message_id:
            raise WhatsAppProviderError("META_MESSAGE_ID_MISSING", "Meta no devolvió ID de mensaje.")
        return {"id": message_id}


def provider_client(provider_name, *, recipient_phone="", session=None):
    provider = str(provider_name or "mock").strip().lower()
    if provider == "mock":
        return MockWhatsAppClient()
    if provider != "meta":
        raise WhatsAppProviderError("PROVIDER_NOT_SUPPORTED", "Proveedor no soportado.")
    readiness = meta_external_writes_ready(recipient_phone)
    if not readiness["ready"]:
        raise ExternalWritesDisabled(
            "WHATSAPP_EXTERNAL_WRITES_DISABLED",
            "Las escrituras reales de WhatsApp permanecen deshabilitadas o incompletas.",
        )
    return MetaWhatsAppClient(session=session)

