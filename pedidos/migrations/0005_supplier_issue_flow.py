from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pedidos", "0004_alter_supplierresponseevent_action")]

    operations = [
        migrations.AlterField(
            model_name="shipment",
            name="supplier_state",
            field=models.CharField(
                choices=[
                    ("pending_response", "Pendiente de respuesta"),
                    ("received", "Pedido recibido"),
                    ("ready_for_guide", "Listo para despacho"),
                    ("issue_reported", "Novedad reportada"),
                ],
                db_index=True,
                default="pending_response",
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="shipment",
            name="guide_delivery_state",
            field=models.CharField(
                choices=[
                    ("not_requested", "No solicitada"),
                    ("requested", "Solicitada"),
                    ("ready_to_send", "Lista para enviar"),
                    ("sent", "Enviada"),
                    ("failed", "Fallo de envío"),
                ],
                db_index=True,
                default="not_requested",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="supplierresponseevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("order_received", "Pedido recibido"),
                    ("request_guide", "Listo para despacho"),
                    ("report_stockout", "Agotado"),
                    ("report_issue", "Reportar novedad"),
                    ("select_issue_item", "Seleccionar SKU afectado"),
                    ("provide_issue_quantity", "Informar cantidad afectada"),
                    ("classify_issue", "Clasificar novedad"),
                    ("provide_issue_detail", "Detallar novedad"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="supplierresponseevent",
            name="result",
            field=models.CharField(
                choices=[
                    ("applied", "Aplicada"),
                    ("replayed", "Repetida"),
                    ("review", "Requiere revisión"),
                    ("rejected", "Rechazada"),
                ],
                default="applied",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="shipmentnovelty",
            name="category",
            field=models.CharField(
                choices=[
                    ("supplier_pending_detail", "Proveedor debe detallar la novedad"),
                    ("supplier_stockout", "Agotado total"),
                    ("supplier_partial", "Faltante parcial"),
                    ("supplier_damage", "Producto averiado"),
                    ("supplier_guide_issue", "Problema con la guía"),
                    ("supplier_delay", "Retraso de despacho"),
                    ("supplier_not_recognized", "Pedido no reconocido"),
                    ("supplier_other", "Otra novedad"),
                ],
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="shipmentnovelty",
            name="detail_state",
            field=models.CharField(
                choices=[
                    ("awaiting_category", "Pendiente de categoría"),
                    ("awaiting_item", "Pendiente de SKU"),
                    ("awaiting_quantity", "Pendiente de cantidad"),
                    ("awaiting_detail", "Pendiente de detalle"),
                    ("complete", "Detalle completo"),
                ],
                db_index=True,
                default="awaiting_category",
                max_length=24,
            ),
        ),
    ]
