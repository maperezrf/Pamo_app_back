from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("communications", "0002_whatsappchannelconfig")]

    operations = [
        migrations.AddField(
            model_name="whatsappdraft",
            name="message_kind",
            field=models.CharField(
                choices=[
                    ("supplier_order", "Nuevo despacho"),
                    ("guide_delivery", "Entrega de guía"),
                    ("novelty_menu", "Menú de novedad"),
                    ("novelty_prompt", "Solicitud de detalle"),
                    ("issue_sku_menu", "Selección de SKU afectado"),
                    ("issue_quantity_prompt", "Cantidad afectada"),
                    ("novelty_confirmation", "Confirmación de novedad"),
                    ("internal_order_copy", "Copia interna de pedido"),
                ],
                db_index=True,
                default="supplier_order",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="whatsappdraft",
            name="interactive_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="whatsappdraft",
            name="auto_prepared",
            field=models.BooleanField(default=False),
        ),
    ]
