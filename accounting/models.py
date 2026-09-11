from django.conf import settings
from django.db import models

from logistics.models import Remittance, RemittanceLine


class RemittanceInvoiceLine(models.Model):
    """Datos de facturación de una línea de remisión -- separados de
    logistics.RemittanceLine porque son de uso exclusivo de contabilidad
    (SKU y precio en Siigo, no lo que se le compró al proveedor)."""

    remittance_line = models.OneToOneField(RemittanceLine, on_delete=models.CASCADE, related_name="invoice_line")
    siigo_sku = models.CharField(max_length=120, blank=True)
    invoice_description = models.CharField(max_length=500, blank=True)
    invoice_unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    override_reason = models.CharField(max_length=500, blank=True)


class RemittanceInvoiceAttempt(models.Model):
    class Status(models.TextChoices):
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
