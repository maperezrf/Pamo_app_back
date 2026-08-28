from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0014_shopify_sync_outbox"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorylevel",
            name="origin_address",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="inventorylevel",
            name="address_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="inventorylevel",
            name="fulfills_online_orders",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="inventorylevel",
            name="location_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="logisticsquotesnapshot",
            name="delivery_estimate",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
