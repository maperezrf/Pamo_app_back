import django.db.models.deletion
import facturacion.models
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0002_seed_reference_data"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="remittance",
            name="supplier_freight_cost",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="remittance",
            name="supplier_global_discount_percent",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name="remittance",
            name="supplier_global_discount_value",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="remittance",
            name="supplier_other_charges",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="remittanceline",
            name="supplier_discount_percent",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name="remittanceline",
            name="supplier_discount_value",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="remittanceline",
            name="supplier_line_total",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.CreateModel(
            name="RemittanceSupplierInvoiceFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("file", models.FileField(upload_to=facturacion.models.supplier_invoice_upload_to)),
                ("original_name", models.CharField(max_length=180)),
                ("mime_type", models.CharField(max_length=120)),
                ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("remittance", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_invoice_files", to="facturacion.remittance")),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("remittance", "sha256"), name="unique_supplier_invoice_per_remittance_hash"),
                ],
            },
        ),
    ]
