import uuid

from django.db import models


class WhatsAppChannelConfig(models.Model):
    CONNECTION_STATES = [
        ("not_linked", "Sin vincular"),
        ("observed", "Observado en Meta"),
        ("ready", "Listo"),
        ("blocked", "Bloqueado"),
    ]
    QUALITY_RATINGS = [
        ("unknown", "Sin datos"),
        ("high", "Alta"),
        ("medium", "Media"),
        ("low", "Baja"),
    ]
    WEBHOOK_STATES = [
        ("not_configured", "Sin configurar"),
        ("pending", "Pendiente"),
        ("verified", "Verificado"),
        ("error", "Con error"),
    ]

    slug = models.CharField(max_length=40, unique=True, default="primary")
    provider = models.CharField(max_length=40, default="meta_cloud_api")
    partner_name = models.CharField(max_length=120, blank=True)
    display_name = models.CharField(max_length=160, blank=True)
    business_id = models.CharField(max_length=120, blank=True)
    waba_id = models.CharField(max_length=120, blank=True)
    phone_number_id = models.CharField(max_length=120, blank=True)
    display_phone_number = models.CharField(max_length=40, blank=True)
    connection_state = models.CharField(
        max_length=24, choices=CONNECTION_STATES, default="not_linked"
    )
    quality_rating = models.CharField(
        max_length=24, choices=QUALITY_RATINGS, default="unknown"
    )
    webhook_state = models.CharField(
        max_length=24, choices=WEBHOOK_STATES, default="not_configured"
    )
    active = models.BooleanField(default=False)
    updated_by = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WhatsAppDraft(models.Model):
    MESSAGE_KINDS = [
        ("supplier_order", "Nuevo despacho"),
        ("guide_delivery", "Entrega de guía"),
        ("novelty_menu", "Menú de novedad"),
        ("novelty_prompt", "Solicitud de detalle"),
        ("issue_sku_menu", "Selección de SKU afectado"),
        ("issue_quantity_prompt", "Cantidad afectada"),
        ("novelty_confirmation", "Confirmación de novedad"),
        ("internal_order_copy", "Copia interna de pedido"),
    ]
    STATES = [
        ("draft", "Borrador"),
        ("approved", "Aprobado"),
        ("queued", "En cola"),
        ("sent", "Enviado"),
        ("delivered", "Entregado"),
        ("read", "Leído"),
        ("failed", "Fallido"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_module = models.CharField(max_length=60, default="pedidos")
    source_type = models.CharField(max_length=60, default="shipment")
    source_id = models.CharField(max_length=80, db_index=True)
    message_kind = models.CharField(
        max_length=32, choices=MESSAGE_KINDS, default="supplier_order", db_index=True
    )
    order_visible_id = models.CharField(max_length=160, blank=True)
    warehouse_reference = models.CharField(max_length=160, blank=True)
    contact_reference = models.CharField(max_length=160)
    recipient_name = models.CharField(max_length=160)
    recipient_phone = models.CharField(max_length=32)
    rendered_body = models.TextField()
    interactive_payload = models.JSONField(default=dict, blank=True)
    auto_prepared = models.BooleanField(default=False)
    document_source_id = models.CharField(max_length=80, blank=True)
    document_name = models.CharField(max_length=255, blank=True)
    document_sha256 = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=24, choices=STATES, default="draft", db_index=True)
    created_by = models.CharField(max_length=240)
    approved_by = models.CharField(max_length=240, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source_module", "source_type", "source_id"])]


class WhatsAppOutbox(models.Model):
    STATES = [
        ("pending", "Pendiente"),
        ("sent", "Enviado"),
        ("delivered", "Entregado"),
        ("read", "Leído"),
        ("failed", "Fallido"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.OneToOneField(
        WhatsAppDraft, related_name="outbox", on_delete=models.PROTECT
    )
    provider = models.CharField(max_length=40, default="mock")
    idempotency_key = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=24, choices=STATES, default="pending", db_index=True)
    provider_message_id = models.CharField(max_length=180, blank=True, db_index=True)
    media_id = models.CharField(max_length=180, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=100, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class WhatsAppAttempt(models.Model):
    outbox = models.ForeignKey(
        WhatsAppOutbox, related_name="attempts", on_delete=models.CASCADE
    )
    sequence = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=24)
    error_code = models.CharField(max_length=100, blank=True)
    request_digest = models.CharField(max_length=64)
    provider_reference = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["outbox", "sequence"],
                name="communications_whatsapp_attempt_sequence_unique",
            )
        ]


class WhatsAppWebhookEvent(models.Model):
    provider_event_id = models.CharField(max_length=180, unique=True)
    provider_message_id = models.CharField(max_length=180, blank=True, db_index=True)
    event_type = models.CharField(max_length=60)
    payload_digest = models.CharField(max_length=64)
    waba_id = models.CharField(max_length=120)
    phone_number_id = models.CharField(max_length=120)
    signature_valid = models.BooleanField(default=False)
    duplicate_count = models.PositiveIntegerField(default=0)
    processed = models.BooleanField(default=False)
    error_code = models.CharField(max_length=100, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
