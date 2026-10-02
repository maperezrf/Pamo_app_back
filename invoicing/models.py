from django.db import models

from orders.models import MarketplaceOrder


class Invoice(models.Model):
    """Factura en Siigo de un pedido de marketplace. Una por pedido: el
    `OneToOneField` impide facturar dos veces. Las líneas quedan en
    `request_payload` (el JSON enviado a Siigo), sin tabla propia. Ver
    docs/apps/invoicing.md.
    """

    class Status(models.TextChoices):
        # Se escribe ANTES de llamar a Siigo. Si el proceso muere o la red
        # falla, no se sabe si la factura quedó creada: no se reintenta
        # sola, se revisa en Siigo a mano.
        CREATING = "creando", "Creando"
        CREATED = "creada", "Creada"
        # Siigo rechazó la factura (no existe): se reintenta en la siguiente
        # corrida.
        ERROR = "error", "Error"

    marketplace_order = models.OneToOneField(MarketplaceOrder, on_delete=models.PROTECT, related_name="invoice")
    status = models.CharField(max_length=20, choices=Status.choices)
    customer_identification = models.CharField(max_length=32)
    customer_name = models.CharField(max_length=255)
    purchase_order_number = models.CharField(max_length=64, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2)
    reteiva = models.DecimalField(max_digits=14, decimal_places=2)
    reteica = models.DecimalField(max_digits=14, decimal_places=2)
    retefuente = models.DecimalField(max_digits=14, decimal_places=2)
    # Valor del pago enviado a Siigo.
    total = models.DecimalField(max_digits=14, decimal_places=2)
    # `stamp.send` enviado (timbrar ante la DIAN).
    stamped = models.BooleanField()
    siigo_id = models.CharField(max_length=64, blank=True)
    # Número visible en Siigo (`name`), ej. FV-1-1234.
    number = models.CharField(max_length=32, blank=True)
    siigo_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Total que Siigo calculó al rechazar por "The total payments must be
    # equal to the total invoice": el reintento lo envía como pago.
    siigo_calculated_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    invoice_date = models.DateField()
    error_message = models.TextField(blank=True)
    request_payload = models.JSONField(default=dict)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Factura"
        verbose_name_plural = "Facturas"

    def __str__(self):
        return self.number or f"{self.marketplace_order} ({self.status})"
