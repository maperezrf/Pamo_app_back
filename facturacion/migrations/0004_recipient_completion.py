import decimal
import uuid

import django.core.validators
import django.db.models.deletion
import facturacion.models
from django.conf import settings
from django.db import migrations, models


def populate_public_line_ids(apps, schema_editor):
    RemittanceLine = apps.get_model("facturacion", "RemittanceLine")
    for line in RemittanceLine.objects.filter(public_id__isnull=True).iterator():
        line.public_id = uuid.uuid4()
        line.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0003_supplier_invoice_private_capture"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="remittanceline",
            name="public_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_public_line_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="remittanceline",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="remittancedelivery",
            name="recipient_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.CreateModel(
            name="RemittanceShareLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("purpose", models.CharField(choices=[("RECIPIENT_COMPLETION", "Firma y destinos del cliente")], default="RECIPIENT_COMPLETION", max_length=32)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("remittance", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="share_links", to="facturacion.remittance")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="RemittanceUsageDestination",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.CharField(max_length=180)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usage_destinations", to="facturacion.remittanceparty")),
            ],
            options={
                "ordering": ["value"],
                "constraints": [models.UniqueConstraint(fields=("customer", "value"), name="unique_customer_usage_destination")],
            },
        ),
        migrations.CreateModel(
            name="RemittanceRecipientAcceptance",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("signer_name", models.CharField(max_length=160)),
                ("signature_file", models.FileField(upload_to=facturacion.models.recipient_signature_upload_to)),
                ("signature_mime_type", models.CharField(default="image/png", max_length=40)),
                ("signature_size_bytes", models.PositiveIntegerField()),
                ("signature_sha256", models.CharField(max_length=64)),
                ("idempotency_key", models.UUIDField(unique=True)),
                ("signed_at", models.DateTimeField(auto_now_add=True)),
                ("remittance", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="recipient_acceptance", to="facturacion.remittance")),
                ("share_link", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="acceptance", to="facturacion.remittancesharelink")),
            ],
        ),
        migrations.CreateModel(
            name="RemittanceRecipientAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12, validators=[django.core.validators.MinValueValidator(decimal.Decimal("0.001"))])),
                ("destination", models.CharField(max_length=180)),
                ("acceptance", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allocations", to="facturacion.remittancerecipientacceptance")),
                ("line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recipient_allocations", to="facturacion.remittanceline")),
            ],
            options={"ordering": ["line__line_number", "id"]},
        ),
    ]
