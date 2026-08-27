import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0011_catalog_query_indexes")]

    operations = [
        migrations.CreateModel(
            name="ExternalChannelProductSnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("channel", models.CharField(choices=[("SHOPIFY", "Shopify"), ("MERCADO_LIBRE", "Mercado Libre"), ("FALABELLA", "Falabella"), ("SODIMAC", "Sodimac"), ("MADECENTRO", "Madecentro"), ("RAPPI", "Rappi")], max_length=32)),
                ("external_product_id", models.CharField(max_length=160)),
                ("external_variant_id", models.CharField(blank=True, max_length=160)),
                ("sku", models.CharField(blank=True, db_index=True, max_length=160)),
                ("barcode", models.CharField(blank=True, db_index=True, max_length=160)),
                ("title", models.CharField(blank=True, max_length=500)),
                ("brand", models.CharField(blank=True, max_length=180)),
                ("category", models.CharField(blank=True, max_length=240)),
                ("state", models.CharField(blank=True, max_length=80)),
                ("price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("inventory_available", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("currency", models.CharField(default="COP", max_length=3)),
                ("url", models.URLField(blank=True, max_length=2048)),
                ("image_url", models.URLField(blank=True, max_length=2048)),
                ("match_status", models.CharField(choices=[("EXACT_SKU", "SKU exacto Shopify"), ("DUPLICATE_SKU", "SKU duplicado en el canal"), ("AMBIGUOUS_SKU", "SKU ambiguo en Shopify"), ("IDENTIFIER_REVIEW", "Otro identificador por revisar"), ("MISSING_SHOPIFY", "Ausente en Shopify"), ("MISSING_SKU", "Sin SKU verificable"), ("STALE", "Ausente en la lectura más reciente")], max_length=32)),
                ("match_reason", models.CharField(blank=True, max_length=500)),
                ("candidate_variant_ids", models.JSONField(blank=True, default=list)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("observed_at", models.DateTimeField()),
                ("source_updated_at", models.DateTimeField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("matched_variant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="external_channel_snapshots", to="catalogo.productvariant")),
            ],
            options={"ordering": ["channel", "sku", "external_product_id", "external_variant_id"]},
        ),
        migrations.AddConstraint(
            model_name="externalchannelproductsnapshot",
            constraint=models.UniqueConstraint(fields=("channel", "external_product_id", "external_variant_id"), name="unique_external_channel_product_variant"),
        ),
        migrations.AddIndex(model_name="externalchannelproductsnapshot", index=models.Index(fields=["channel", "match_status", "active"], name="catalog_ext_match_idx")),
        migrations.AddIndex(model_name="externalchannelproductsnapshot", index=models.Index(fields=["channel", "sku"], name="catalog_ext_sku_idx")),
        migrations.AddIndex(model_name="externalchannelproductsnapshot", index=models.Index(fields=["channel", "state"], name="catalog_ext_state_idx")),
    ]
