from django.db import migrations, models
import django.db.models.deletion


def require_unique_contact_phones(apps, schema_editor):
    MessagingContact = apps.get_model("pedidos", "MessagingContact")
    duplicates = (
        MessagingContact.objects.values("config_id", "phone")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            "Hay telefonos duplicados por bodega. Auditalos antes de aplicar esta migracion."
        )


class Migration(migrations.Migration):
    dependencies = [("pedidos", "0002_supplier_responses")]

    operations = [
        migrations.AlterField(
            model_name="supplierresponseevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("order_received", "Pedido recibido"),
                    ("request_guide", "Listo, enviar guia"),
                    ("report_issue", "Reportar novedad"),
                    ("classify_issue", "Clasificar novedad"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="shipmentnovelty",
            name="detail_state",
            field=models.CharField(
                choices=[
                    ("awaiting_category", "Pendiente de categoria"),
                    ("awaiting_detail", "Pendiente de detalle"),
                    ("complete", "Detalle completo"),
                ],
                db_index=True,
                default="awaiting_category",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="messagingconfig",
            name="updated_by",
            field=models.CharField(blank=True, max_length=240),
        ),
        migrations.RunPython(
            require_unique_contact_phones,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="messagingcontact",
            constraint=models.UniqueConstraint(
                fields=("config", "phone"),
                name="pedidos_messaging_contact_phone_unique",
            ),
        ),
        migrations.AddField(
            model_name="manualfollowup",
            name="contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="manual_followups",
                to="pedidos.messagingcontact",
            ),
        ),
        migrations.AddField(
            model_name="manualfollowup",
            name="preparation_key",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="manualfollowup",
            name="shipment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="manual_followups",
                to="pedidos.shipment",
            ),
        ),
    ]
