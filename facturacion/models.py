import uuid
from pathlib import Path
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class RemittanceWarehouse(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class RemittanceSequence(models.Model):
    key = models.CharField(max_length=40, unique=True, default="RD")
    last_value = models.PositiveBigIntegerField(default=0)


class RemittanceParty(models.Model):
    class PartyType(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Cliente"
        SUPPLIER = "SUPPLIER", "Proveedor"

    party_type = models.CharField(max_length=16, choices=PartyType.choices)
    siigo_id = models.CharField(max_length=120, blank=True)
    nit = models.CharField(max_length=32)
    name = models.CharField(max_length=180)
    is_validated = models.BooleanField(default=False)
    cached_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["party_type", "nit"], name="unique_remittance_party_nit_type"),
        ]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.nit = "".join(character for character in self.nit if character.isdigit())
        self.name = self.name.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} · {self.nit}"


class RemittanceFavorite(models.Model):
    party = models.OneToOneField(RemittanceParty, on_delete=models.PROTECT, related_name="favorite")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    requires_validation = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "party__name"]


class AuthorizedPerson(models.Model):
    customer = models.ForeignKey(RemittanceParty, on_delete=models.PROTECT, related_name="authorized_people")
    name = models.CharField(max_length=160)
    document = models.CharField(max_length=40, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.name = self.name.strip().upper()
        super().save(*args, **kwargs)


class Remittance(models.Model):
    class DocumentStatus(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        CONFIRMED = "CONFIRMED", "Confirmada"
        CANCELLED = "CANCELLED", "Anulada"

    class DeliveryStatus(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        COMPLETED = "COMPLETED", "Completada"

    class InvoiceStatus(models.TextChoices):
        PENDING_CODING = "PENDING_CODING", "Pendiente de codificación"
        READY = "READY", "Lista para facturar"
        INVOICING = "INVOICING", "Facturando"
        INVOICED = "INVOICED", "Facturada"
        ERROR = "ERROR", "Error de facturación"

    class CommunicationStatus(models.TextChoices):
        NOT_PREPARED = "NOT_PREPARED", "No preparada"
        PREPARED = "PREPARED", "Preparada"
        SENT = "SENT", "Enviada"
        ERROR = "ERROR", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=24, unique=True, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    warehouse = models.ForeignKey(RemittanceWarehouse, on_delete=models.PROTECT, related_name="remittances")
    supplier = models.ForeignKey(RemittanceParty, on_delete=models.PROTECT, related_name="supplied_remittances")
    customer = models.ForeignKey(RemittanceParty, on_delete=models.PROTECT, related_name="customer_remittances")
    requester_name = models.CharField(max_length=160)
    requester_document = models.CharField(max_length=40, blank=True)
    document_status = models.CharField(max_length=16, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT)
    delivery_status = models.CharField(max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    invoice_status = models.CharField(max_length=24, choices=InvoiceStatus.choices, default=InvoiceStatus.PENDING_CODING)
    communication_status = models.CharField(max_length=24, choices=CommunicationStatus.choices, default=CommunicationStatus.NOT_PREPARED)
    supplier_global_discount_percent = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    supplier_global_discount_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    supplier_other_charges = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    supplier_freight_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_remittances")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.requester_name = self.requester_name.strip().upper()
        super().save(*args, **kwargs)


class RemittanceLine(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    remittance = models.ForeignKey(Remittance, on_delete=models.CASCADE, related_name="lines")
    line_number = models.PositiveIntegerField()
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    original_description = models.CharField(max_length=500)
    usage_destination = models.CharField(max_length=180, blank=True)
    supplier_sku = models.CharField(max_length=120, blank=True)
    supplier_unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    supplier_line_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    supplier_discount_percent = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    supplier_discount_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    siigo_sku = models.CharField(max_length=120, blank=True)
    invoice_description = models.CharField(max_length=500, blank=True)
    invoice_unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    override_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["line_number"]
        constraints = [
            models.UniqueConstraint(fields=["remittance", "line_number"], name="unique_remittance_line_number"),
        ]

    def save(self, *args, **kwargs):
        self.original_description = self.original_description.strip().upper()
        self.usage_destination = self.usage_destination.strip().upper()
        super().save(*args, **kwargs)


class RemittanceDelivery(models.Model):
    class Method(models.TextChoices):
        PERSONAL_PICKUP = "PERSONAL_PICKUP", "Retira personalmente"
        CARRIER = "CARRIER", "Transportadora"
        UBER = "UBER", "Uber"
        INDRIVE = "INDRIVE", "InDrive"
        MESSENGER = "MESSENGER", "Mensajería / domiciliario"
        OTHER = "OTHER", "Otro"

    remittance = models.OneToOneField(Remittance, on_delete=models.CASCADE, related_name="delivery")
    method = models.CharField(max_length=24, choices=Method.choices)
    provider_name = models.CharField(max_length=160, blank=True)
    tracking_number = models.CharField(max_length=160, blank=True)
    driver_name = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    recipient_name = models.CharField(max_length=160, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class RemittanceUsageDestination(models.Model):
    customer = models.ForeignKey(RemittanceParty, on_delete=models.PROTECT, related_name="usage_destinations")
    value = models.CharField(max_length=180)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["value"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "value"], name="unique_customer_usage_destination"),
        ]

    def save(self, *args, **kwargs):
        self.value = self.value.strip().upper()
        super().save(*args, **kwargs)


class RemittanceShareLink(models.Model):
    class Purpose(models.TextChoices):
        RECIPIENT_COMPLETION = "RECIPIENT_COMPLETION", "Firma y destinos del cliente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remittance = models.ForeignKey(Remittance, on_delete=models.PROTECT, related_name="share_links")
    purpose = models.CharField(max_length=32, choices=Purpose.choices, default=Purpose.RECIPIENT_COMPLETION)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


def recipient_signature_upload_to(instance, filename):
    return f"remittances/{instance.remittance_id}/recipient-signatures/{uuid.uuid4().hex}.png"


class RemittanceRecipientAcceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remittance = models.OneToOneField(Remittance, on_delete=models.PROTECT, related_name="recipient_acceptance")
    share_link = models.OneToOneField(RemittanceShareLink, on_delete=models.PROTECT, related_name="acceptance")
    signer_name = models.CharField(max_length=160)
    signature_file = models.FileField(upload_to=recipient_signature_upload_to)
    signature_mime_type = models.CharField(max_length=40, default="image/png")
    signature_size_bytes = models.PositiveIntegerField()
    signature_sha256 = models.CharField(max_length=64)
    idempotency_key = models.UUIDField(unique=True)
    signed_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.signer_name = self.signer_name.strip().upper()
        super().save(*args, **kwargs)


class RemittanceRecipientAllocation(models.Model):
    acceptance = models.ForeignKey(RemittanceRecipientAcceptance, on_delete=models.CASCADE, related_name="allocations")
    line = models.ForeignKey(RemittanceLine, on_delete=models.PROTECT, related_name="recipient_allocations")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    destination = models.CharField(max_length=180)

    class Meta:
        ordering = ["line__line_number", "id"]

    def save(self, *args, **kwargs):
        self.destination = self.destination.strip().upper()
        super().save(*args, **kwargs)


class RemittanceAuditEvent(models.Model):
    remittance = models.ForeignKey(Remittance, on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=80)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class RemittanceInvoiceAttempt(models.Model):
    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "Vista previa"
        PENDING = "PENDING", "Pendiente"
        UNKNOWN_RESULT = "UNKNOWN_RESULT", "Resultado desconocido"
        SUCCEEDED = "SUCCEEDED", "Exitosa"
        FAILED = "FAILED", "Fallida"

    remittance = models.ForeignKey(Remittance, on_delete=models.PROTECT, related_name="invoice_attempts")
    idempotency_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    external_invoice_id = models.CharField(max_length=160, blank=True)
    external_number = models.CharField(max_length=80, blank=True)
    sanitized_result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


def supplier_invoice_upload_to(instance, filename):
    extension = Path(filename).suffix.lower().lstrip(".") or "bin"
    return f"remittances/{instance.remittance_id}/supplier-invoices/{uuid.uuid4().hex}.{extension}"


class RemittanceSupplierInvoiceFile(models.Model):
    """Adjunto contable privado; nunca se publica en el documento del cliente."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remittance = models.ForeignKey(
        Remittance,
        on_delete=models.PROTECT,
        related_name="supplier_invoice_files",
    )
    file = models.FileField(upload_to=supplier_invoice_upload_to)
    original_name = models.CharField(max_length=180)
    mime_type = models.CharField(max_length=120)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["remittance", "sha256"],
                name="unique_supplier_invoice_per_remittance_hash",
            ),
        ]
