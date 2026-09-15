import base64
import hashlib
import io
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from facturacion.models import (
    Remittance,
    RemittanceAuditEvent,
    RemittanceRecipientAcceptance,
    RemittanceRecipientAllocation,
    RemittanceShareLink,
    RemittanceUsageDestination,
)


PNG_HEADER = b"\x89PNG\r\n\x1a\n"
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


class RecipientCompletionError(Exception):
    def __init__(self, detail, *, code, status_code=400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def prepare_recipient_link(remittance, actor, *, lifetime_days=7):
    if remittance.document_status != Remittance.DocumentStatus.CONFIRMED:
        raise RecipientCompletionError(
            "Confirma la remisión antes de compartirla.",
            code="REMITTANCE_NOT_CONFIRMED",
            status_code=409,
        )
    if hasattr(remittance, "recipient_acceptance"):
        raise RecipientCompletionError(
            "Esta remisión ya fue firmada por el cliente.",
            code="REMITTANCE_ALREADY_SIGNED",
            status_code=409,
        )

    token = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        RemittanceShareLink.objects.filter(
            remittance=remittance,
            purpose=RemittanceShareLink.Purpose.RECIPIENT_COMPLETION,
            completed_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
        share = RemittanceShareLink.objects.create(
            remittance=remittance,
            token_hash=hash_token(token),
            expires_at=now + timedelta(days=lifetime_days),
            created_by=actor,
        )
        remittance.communication_status = Remittance.CommunicationStatus.PREPARED
        remittance.version += 1
        remittance.save(update_fields=["communication_status", "version", "updated_at"])
        RemittanceAuditEvent.objects.create(
            remittance=remittance,
            event_type="RECIPIENT_SHARE_PREPARED",
            actor=actor,
            details={"share_link_id": str(share.id), "expires_at": share.expires_at.isoformat()},
        )
    return share, token


def find_share(token, *, lock=False):
    if not isinstance(token, str) or not 40 <= len(token) <= 60:
        raise RecipientCompletionError("El enlace no es válido.", code="INVALID_LINK", status_code=404)
    queryset = RemittanceShareLink.objects.select_related(
        "remittance__customer", "remittance__delivery", "created_by"
    ).prefetch_related("remittance__lines", "remittance__customer__usage_destinations")
    if lock:
        queryset = queryset.select_for_update()
    try:
        share = queryset.get(
            token_hash=hash_token(token),
            purpose=RemittanceShareLink.Purpose.RECIPIENT_COMPLETION,
        )
    except RemittanceShareLink.DoesNotExist as error:
        raise RecipientCompletionError("El enlace no es válido.", code="INVALID_LINK", status_code=404) from error
    if share.revoked_at:
        raise RecipientCompletionError("Este enlace fue reemplazado.", code="LINK_REVOKED", status_code=410)
    if share.expires_at <= timezone.now():
        raise RecipientCompletionError("Este enlace venció. Solicita uno nuevo.", code="LINK_EXPIRED", status_code=410)
    return share


def public_recipient_form(token):
    share = find_share(token)
    remittance = share.remittance
    acceptance = getattr(remittance, "recipient_acceptance", None)
    return {
        "rdNumber": remittance.number,
        "clientName": remittance.customer.name,
        "suggestedSigner": remittance.requester_name,
        "status": "SIGNED" if acceptance else "PENDING",
        "signerName": acceptance.signer_name if acceptance else "",
        "signedAt": acceptance.signed_at if acceptance else None,
        "destinations": list(
            remittance.customer.usage_destinations.filter(is_active=True).values_list("value", flat=True)
        ),
        "lines": [
            {
                "id": str(line.public_id),
                "quantity": str(line.quantity),
                "description": line.original_description,
                "currentDestination": line.usage_destination,
            }
            for line in remittance.lines.all()
        ],
    }


def _decode_signature(signature):
    if not isinstance(signature, dict) or signature.get("mimeType") != "image/png":
        raise RecipientCompletionError("La firma debe enviarse como PNG.", code="INVALID_SIGNATURE")
    encoded = signature.get("base64", "")
    if encoded.startswith("data:image/png;base64,"):
        encoded = encoded.split(",", 1)[1]
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise RecipientCompletionError("La firma no es válida.", code="INVALID_SIGNATURE") from error
    if not body.startswith(PNG_HEADER) or len(body) < 50 or len(body) > MAX_SIGNATURE_BYTES:
        raise RecipientCompletionError("La firma no es válida o supera 2 MB.", code="INVALID_SIGNATURE")
    try:
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            image.verify()
        if not 2 <= width <= 4096 or not 2 <= height <= 4096 or width * height > 4_000_000:
            raise RecipientCompletionError("La firma tiene dimensiones no válidas.", code="INVALID_SIGNATURE")
    except RecipientCompletionError:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise RecipientCompletionError("La imagen de la firma está dañada.", code="INVALID_SIGNATURE") from error
    return body


def _decimal(value, field):
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise RecipientCompletionError(f"{field} no es válida.", code="INVALID_ALLOCATION") from error
    if parsed <= 0:
        raise RecipientCompletionError(f"{field} debe ser mayor que cero.", code="INVALID_ALLOCATION")
    return parsed


def accept_recipient_completion(token, payload):
    signer_name = str(payload.get("signerName", "")).strip().upper()
    if not signer_name or len(signer_name) > 160:
        raise RecipientCompletionError("Escribe el nombre de quien firma.", code="SIGNER_REQUIRED")
    try:
        idempotency_key = payload.get("idempotencyKey")
        idempotency_key = uuid.UUID(str(idempotency_key))
    except (TypeError, ValueError, AttributeError) as error:
        raise RecipientCompletionError("La clave de confirmación no es válida.", code="INVALID_IDEMPOTENCY_KEY") from error
    signature_body = _decode_signature(payload.get("signature"))
    allocations_payload = payload.get("allocations")
    if not isinstance(allocations_payload, list) or not allocations_payload:
        raise RecipientCompletionError("Asigna el destino de todos los productos.", code="ALLOCATIONS_REQUIRED")

    with transaction.atomic():
        previous = RemittanceRecipientAcceptance.objects.filter(idempotency_key=idempotency_key).first()
        if previous:
            return previous, False
        share = find_share(token, lock=True)
        remittance = Remittance.objects.select_for_update().select_related("customer", "delivery").get(pk=share.remittance_id)
        if hasattr(remittance, "recipient_acceptance") or share.completed_at:
            raise RecipientCompletionError("Esta remisión ya fue firmada.", code="REMITTANCE_ALREADY_SIGNED", status_code=409)

        lines = {str(line.public_id): line for line in remittance.lines.select_for_update().all()}
        grouped = {line_id: [] for line_id in lines}
        for raw in allocations_payload:
            line_id = str(raw.get("lineId", ""))
            if line_id not in lines:
                raise RecipientCompletionError("Un producto no pertenece a esta remisión.", code="INVALID_ALLOCATION")
            destination = str(raw.get("destination", "")).strip().upper()
            if not destination or len(destination) > 180:
                raise RecipientCompletionError("Completa un destino válido para cada producto.", code="INVALID_ALLOCATION")
            grouped[line_id].append((_decimal(raw.get("quantity"), "La cantidad"), destination))

        for line_id, line in lines.items():
            if not grouped[line_id] or sum((quantity for quantity, _ in grouped[line_id]), Decimal("0")) != line.quantity:
                raise RecipientCompletionError(
                    f"Distribuye exactamente {line.quantity} unidad(es) de {line.original_description}.",
                    code="ALLOCATION_MISMATCH",
                )

        signature_hash = hashlib.sha256(signature_body).hexdigest()
        acceptance = RemittanceRecipientAcceptance(
            remittance=remittance,
            share_link=share,
            signer_name=signer_name,
            signature_size_bytes=len(signature_body),
            signature_sha256=signature_hash,
            idempotency_key=idempotency_key,
        )
        acceptance.signature_file.save("firma.png", ContentFile(signature_body), save=False)
        acceptance.save()

        for line_id, entries in grouped.items():
            line = lines[line_id]
            summaries = []
            for quantity, destination in entries:
                RemittanceRecipientAllocation.objects.create(
                    acceptance=acceptance,
                    line=line,
                    quantity=quantity,
                    destination=destination,
                )
                RemittanceUsageDestination.objects.get_or_create(customer=remittance.customer, value=destination)
                summaries.append(destination if quantity == line.quantity else f"{quantity:g} × {destination}")
            line.usage_destination = "; ".join(summaries)[:180]
            line.save(update_fields=["usage_destination"])

        now = timezone.now()
        remittance.delivery_status = Remittance.DeliveryStatus.COMPLETED
        remittance.version += 1
        remittance.save(update_fields=["delivery_status", "version", "updated_at"])
        remittance.delivery.recipient_name = signer_name
        remittance.delivery.completed_at = now
        remittance.delivery.save(update_fields=["recipient_name", "completed_at"])
        share.completed_at = now
        share.save(update_fields=["completed_at"])
        RemittanceAuditEvent.objects.create(
            remittance=remittance,
            event_type="RECIPIENT_SIGNED",
            actor=share.created_by,
            details={
                "source": "public-recipient-link",
                "signer_name": signer_name,
                "signature_sha256": signature_hash,
            },
        )
    return acceptance, True
