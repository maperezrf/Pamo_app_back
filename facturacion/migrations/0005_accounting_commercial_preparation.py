from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("facturacion", "0004_recipient_completion")]

    operations = [
        migrations.AddField(
            model_name="remittance",
            name="default_margin_percent",
            field=models.DecimalField(decimal_places=3, default=Decimal("35.000"), max_digits=6),
        ),
        migrations.AddField(
            model_name="remittanceline",
            name="invoice_margin_percent",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=6, null=True),
        ),
    ]
