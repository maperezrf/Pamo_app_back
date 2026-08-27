import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0013_sodimackitimportbatch_and_more")]

    operations = [
        migrations.CreateModel(
            name="ShopifySyncPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="PRIMARY", max_length=32, unique=True)),
                ("environment", models.CharField(choices=[("BETA", "Beta"), ("PRODUCTION", "Producción")], default="BETA", max_length=16)),
                ("scan_enabled", models.BooleanField(default=True)),
                ("writes_enabled", models.BooleanField(default=False)),
                ("price_enabled", models.BooleanField(default=True)),
                ("inventory_enabled", models.BooleanField(default=True)),
                ("maximum_batch_size", models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(25)])),
                ("debounce_seconds", models.PositiveIntegerField(default=60)),
                ("source_max_age_minutes", models.PositiveIntegerField(default=360)),
                ("allowlisted_skus", models.JSONField(blank=True, default=list)),
                ("updated_by", models.CharField(default="local-operator", max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="ShopifySyncRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("mode", models.CharField(choices=[("SCAN", "Detección local"), ("PREVIEW", "Vista previa"), ("EXECUTE", "Ejecución Shopify")], max_length=16)),
                ("status", models.CharField(choices=[("RUNNING", "En curso"), ("SUCCEEDED", "Completada"), ("PARTIAL", "Parcial"), ("BLOCKED", "Bloqueada"), ("FAILED", "Fallida")], default="RUNNING", max_length=16)),
                ("trigger", models.CharField(default="MANUAL_PREVIEW", max_length=40)),
                ("requested_skus", models.JSONField(blank=True, default=list)),
                ("scanned_count", models.PositiveIntegerField(default=0)),
                ("ready_count", models.PositiveIntegerField(default=0)),
                ("blocked_count", models.PositiveIntegerField(default=0)),
                ("no_change_count", models.PositiveIntegerField(default=0)),
                ("succeeded_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_message", models.CharField(blank=True, max_length=300)),
                ("external_writes", models.PositiveIntegerField(default=0)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="ShopifySyncItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sku", models.CharField(db_index=True, max_length=160)),
                ("status", models.CharField(choices=[("READY", "Lista para piloto"), ("BLOCKED", "Bloqueada"), ("NO_CHANGE", "Sin cambios"), ("SUCCEEDED", "Sincronizada"), ("FAILED", "Fallida"), ("CONFLICT", "Conflicto concurrente")], max_length=16)),
                ("fields", models.JSONField(blank=True, default=list)),
                ("previous_values", models.JSONField(blank=True, default=dict)),
                ("proposed_values", models.JSONField(blank=True, default=dict)),
                ("source_evidence", models.JSONField(blank=True, default=dict)),
                ("blockers", models.JSONField(blank=True, default=list)),
                ("fingerprint", models.CharField(db_index=True, max_length=64)),
                ("idempotency_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("rollback_payload", models.JSONField(blank=True, default=dict)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
                ("last_error_code", models.CharField(blank=True, max_length=80)),
                ("last_error_message", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="catalogo.shopifysyncrun")),
                ("variant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shopify_sync_items", to="catalogo.productvariant")),
            ],
            options={"ordering": ["run", "sku"]},
        ),
        migrations.AddConstraint(
            model_name="shopifysyncitem",
            constraint=models.UniqueConstraint(fields=("run", "variant"), name="unique_shopify_sync_variant_run"),
        ),
    ]
