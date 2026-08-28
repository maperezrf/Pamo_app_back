import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class WarehouseLocation(models.Model):
    external_id = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=160)
    reference = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(max_length=40)
    external_id = models.CharField(max_length=160)
    visible_id = models.CharField(max_length=160, db_index=True)
    source_url = models.URLField(max_length=600, blank=True)
    placed_at = models.DateTimeField(db_index=True)
    customer_name = models.CharField(max_length=240, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=40, blank=True)
    currency = models.CharField(max_length=8, default="COP")
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    state = models.CharField(max_length=80, default="open")
    source_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-placed_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_id"],
                name="pedidos_order_channel_external_unique",
            )
        ]
        indexes = [
            models.Index(fields=["channel", "placed_at"]),
            models.Index(fields=["visible_id", "placed_at"]),
        ]

    def __str__(self):
        return f"{self.channel} {self.visible_id}"


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    external_id = models.CharField(max_length=160)
    sku = models.CharField(max_length=160, blank=True, db_index=True)
    name = models.CharField(max_length=400)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    source_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "external_id"],
                name="pedidos_item_order_external_unique",
            )
        ]


class Shipment(models.Model):
    LOGISTICS_STATES = [
        ("without_guide", "Sin guía"),
        ("guide_without_tracking", "Guía generada sin movimiento"),
        ("picked_up", "Recogido"),
        ("in_transit", "En tránsito"),
        ("out_for_delivery", "En reparto"),
        ("delivered", "Entregado"),
        ("exception", "Con novedad"),
        ("returned", "Devuelto"),
        ("logistically_cancelled", "Cancelado logísticamente"),
    ]
    SUPPLIER_STATES = [
        ("pending_response", "Pendiente de respuesta"),
        ("received", "Pedido recibido"),
        ("ready_for_guide", "Listo para enviar guia"),
        ("issue_reported", "Novedad reportada"),
    ]
    GUIDE_DELIVERY_STATES = [
        ("not_requested", "No solicitada"),
        ("requested", "Solicitada"),
        ("ready_to_send", "Lista para enviar"),
        ("sent", "Enviada"),
        ("failed", "Fallo de envio"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, related_name="shipments", on_delete=models.CASCADE)
    external_id = models.CharField(max_length=180)
    warehouse = models.ForeignKey(
        WarehouseLocation,
        related_name="shipments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    warehouse_name = models.CharField(max_length=160, blank=True)
    warehouse_reference = models.CharField(max_length=120, blank=True)
    warehouse_locked = models.BooleanField(default=False)
    warehouse_assignment_source = models.CharField(max_length=40, default="channel")
    carrier = models.CharField(max_length=120, blank=True)
    tracking_number = models.CharField(max_length=180, blank=True, db_index=True)
    tracking_url = models.URLField(max_length=600, blank=True)
    tracking_source = models.CharField(max_length=40, blank=True)
    logistics_state = models.CharField(
        max_length=50,
        choices=LOGISTICS_STATES,
        default="without_guide",
        db_index=True,
    )
    carrier_state_original = models.CharField(max_length=180, blank=True)
    carrier_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    carrier_cost_currency = models.CharField(max_length=8, blank=True)
    carrier_cost_source = models.CharField(max_length=80, blank=True)
    incident_category = models.CharField(max_length=80, blank=True)
    incident_detail = models.TextField(blank=True)
    customer_context = models.TextField(blank=True)
    messaging_state = models.CharField(max_length=60, default="draft")
    supplier_state = models.CharField(
        max_length=40,
        choices=SUPPLIER_STATES,
        default="pending_response",
        db_index=True,
    )
    supplier_state_updated_at = models.DateTimeField(null=True, blank=True)
    guide_delivery_state = models.CharField(
        max_length=32,
        choices=GUIDE_DELIVERY_STATES,
        default="not_requested",
        db_index=True,
    )
    version = models.PositiveIntegerField(default=1)
    source_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "external_id"],
                name="pedidos_shipment_order_external_unique",
            )
        ]

    @property
    def effective_warehouse_name(self):
        return self.warehouse.name if self.warehouse_id else self.warehouse_name


class ShipmentItem(models.Model):
    shipment = models.ForeignKey(Shipment, related_name="shipment_items", on_delete=models.CASCADE)
    order_item = models.ForeignKey(OrderItem, related_name="shipment_items", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "order_item"],
                name="pedidos_shipment_item_unique",
            )
        ]


class TrackingEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, related_name="tracking_events", on_delete=models.CASCADE)
    source = models.CharField(max_length=40)
    external_event_id = models.CharField(max_length=180)
    state_normalized = models.CharField(max_length=60)
    state_original = models.CharField(max_length=180, blank=True)
    description = models.TextField(blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "source", "external_event_id"],
                name="pedidos_tracking_event_unique",
            )
        ]


class LogisticsAudit(models.Model):
    shipment = models.ForeignKey(Shipment, related_name="audit_events", on_delete=models.CASCADE)
    field = models.CharField(max_length=80)
    previous_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    actor = models.CharField(max_length=240)
    source = models.CharField(max_length=40, default="manual")
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class SupplierResponseEvent(models.Model):
    ACTIONS = [
        ("order_received", "Pedido recibido"),
        ("request_guide", "Listo, enviar guia"),
        ("report_issue", "Reportar novedad"),
    ]
    RESULTS = [
        ("applied", "Aplicada"),
        ("replayed", "Repetida"),
        ("review", "Requiere revision"),
        ("rejected", "Rechazada"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(
        Shipment, related_name="supplier_response_events", on_delete=models.CASCADE
    )
    provider_event_id = models.CharField(max_length=180, unique=True)
    action = models.CharField(max_length=32, choices=ACTIONS)
    source = models.CharField(max_length=40, default="whatsapp")
    sender_suffix = models.CharField(max_length=8, blank=True)
    previous_state = models.CharField(max_length=40)
    new_state = models.CharField(max_length=40)
    result = models.CharField(max_length=16, choices=RESULTS, default="applied")
    details = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]


class ShipmentNovelty(models.Model):
    CATEGORIES = [
        ("supplier_pending_detail", "Proveedor debe detallar la novedad"),
        ("supplier_stockout", "Agotado total"),
        ("supplier_partial", "Faltante parcial"),
        ("supplier_delay", "Retraso de despacho"),
        ("supplier_not_recognized", "Pedido no reconocido"),
        ("supplier_other", "Otra novedad"),
    ]
    STATES = [("open", "Abierta"), ("resolved", "Resuelta")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(
        Shipment, related_name="novelties", on_delete=models.CASCADE
    )
    supplier_response = models.OneToOneField(
        SupplierResponseEvent,
        related_name="novelty",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    category = models.CharField(max_length=50, choices=CATEGORIES)
    state = models.CharField(max_length=16, choices=STATES, default="open", db_index=True)
    detail = models.TextField(blank=True)
    affected_items = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=40, default="supplier_whatsapp")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ShipmentDocument(models.Model):
    shipment = models.OneToOneField(Shipment, related_name="document", on_delete=models.CASCADE)
    file = models.FileField(upload_to="pedidos/guias/%Y/%m/")
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.CharField(max_length=240)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class MessagingConfig(models.Model):
    warehouse = models.OneToOneField(
        WarehouseLocation,
        related_name="messaging_config",
        on_delete=models.CASCADE,
    )
    template_body = models.TextField(
        default=(
            "Hola, {{contacto}}.\n\n"
            "Estos son los despachos pendientes de {{bodega}}:\n\n"
            "{{lista_pedidos}}\n\n"
            "Agradecemos confirmar su estado."
        )
    )
    followup_template_body = models.TextField(blank=True)
    maximum_attempts = models.PositiveSmallIntegerField(default=2)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class MessagingContact(models.Model):
    config = models.ForeignKey(MessagingConfig, related_name="contacts", on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=32)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]


class ManualFollowup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.ForeignKey(WarehouseLocation, null=True, on_delete=models.SET_NULL)
    contact_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=32)
    order_numbers = models.JSONField(default=list)
    rendered_message = models.TextField()
    state = models.CharField(max_length=40, default="prepared_manual")
    prepared_by = models.CharField(max_length=240)
    opened_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SavedFilter(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="order_filters", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    filters = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="pedidos_saved_filter_owner_name_unique",
            )
        ]


class IntegrationStatus(models.Model):
    provider = models.CharField(max_length=60, unique=True)
    state = models.CharField(max_length=60, default="disabled_local")
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=120, blank=True)
    records_observed = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider"]
