from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pedidos", "0005_supplier_issue_flow"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="shipping_destination",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="shipment",
            name="shipping_package",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="shipment",
            name="shipping_quote_selection",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="shipment",
            name="guide_request_state",
            field=models.CharField(
                choices=[
                    ("not_ready", "Datos incompletos"),
                    ("ready_to_quote", "Listo para cotizar"),
                    ("quoted", "Cotizado"),
                    ("selected", "Tarifa seleccionada"),
                    ("prepared", "Preparado para generar"),
                    ("created", "Guía creada"),
                    ("failed", "Fallo al generar"),
                ],
                db_index=True,
                default="not_ready",
                max_length=24,
            ),
        ),
    ]
