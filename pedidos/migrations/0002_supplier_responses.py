import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pedidos", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="supplier_state",
            field=models.CharField(
                choices=[
                    ("pending_response", "Pendiente de respuesta"),
                    ("received", "Pedido recibido"),
                    ("ready_for_guide", "Listo para enviar guia"),
                    ("issue_reported", "Novedad reportada"),
                ],
                db_index=True,
                default="pending_response",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="supplier_state_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shipment",
            name="guide_delivery_state",
            field=models.CharField(
                choices=[
                    ("not_requested", "No solicitada"),
                    ("requested", "Solicitada"),
                    ("ready_to_send", "Lista para enviar"),
                    ("sent", "Enviada"),
                    ("failed", "Fallo de envio"),
                ],
                db_index=True,
                default="not_requested",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="SupplierResponseEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider_event_id", models.CharField(max_length=180, unique=True)),
                ("action", models.CharField(choices=[("order_received", "Pedido recibido"), ("request_guide", "Listo, enviar guia"), ("report_issue", "Reportar novedad")], max_length=32)),
                ("source", models.CharField(default="whatsapp", max_length=40)),
                ("sender_suffix", models.CharField(blank=True, max_length=8)),
                ("previous_state", models.CharField(max_length=40)),
                ("new_state", models.CharField(max_length=40)),
                ("result", models.CharField(choices=[("applied", "Aplicada"), ("replayed", "Repetida"), ("review", "Requiere revision"), ("rejected", "Rechazada")], default="applied", max_length=16)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("shipment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="supplier_response_events", to="pedidos.shipment")),
            ],
            options={"ordering": ["-occurred_at"]},
        ),
        migrations.CreateModel(
            name="ShipmentNovelty",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(choices=[("supplier_pending_detail", "Proveedor debe detallar la novedad"), ("supplier_stockout", "Agotado total"), ("supplier_partial", "Faltante parcial"), ("supplier_delay", "Retraso de despacho"), ("supplier_not_recognized", "Pedido no reconocido"), ("supplier_other", "Otra novedad")], max_length=50)),
                ("state", models.CharField(choices=[("open", "Abierta"), ("resolved", "Resuelta")], db_index=True, default="open", max_length=16)),
                ("detail", models.TextField(blank=True)),
                ("affected_items", models.JSONField(blank=True, default=list)),
                ("source", models.CharField(default="supplier_whatsapp", max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("shipment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="novelties", to="pedidos.shipment")),
                ("supplier_response", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="novelty", to="pedidos.supplierresponseevent")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
