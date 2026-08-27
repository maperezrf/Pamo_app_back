from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0010_sodimac_catalog_audit"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="masterproduct",
            index=models.Index(fields=["status", "needs_review"], name="catalog_status_review_idx"),
        ),
        migrations.AddIndex(
            model_name="masterproduct",
            index=models.Index(fields=["vendor", "brand"], name="catalog_vendor_brand_idx"),
        ),
        migrations.AddIndex(
            model_name="masterproduct",
            index=models.Index(fields=["category", "quality_score"], name="catalog_category_quality_idx"),
        ),
        migrations.AddIndex(
            model_name="productvariant",
            index=models.Index(fields=["price"], name="catalog_variant_price_idx"),
        ),
        migrations.AddIndex(
            model_name="siigoproductsnapshot",
            index=models.Index(fields=["match_status", "active"], name="catalog_siigo_match_idx"),
        ),
        migrations.AddIndex(
            model_name="skureconciliation",
            index=models.Index(fields=["status", "reviewed"], name="catalog_reconcile_idx"),
        ),
    ]
