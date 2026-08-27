import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0007_physicalmeasurementimportbatch_and_more")]

    operations = [
        migrations.CreateModel(
            name="PricingLocalBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(choices=[("PREVIEW", "Vista previa"), ("BLOCKED", "Bloqueado por datos o conflictos"), ("APPLIED_LOCAL", "Aplicado solo localmente"), ("REVERSED", "Revertido localmente")], default="PREVIEW", max_length=24)),
                ("selection_snapshot", models.JSONField(default=dict)),
                ("rule_snapshot", models.JSONField(default=list)),
                ("preview_payload", models.JSONField(default=dict)),
                ("rollback_payload", models.JSONField(blank=True, default=dict)),
                ("applied_payload", models.JSONField(blank=True, default=dict)),
                ("actor_label", models.CharField(default="local-operator", max_length=160)),
                ("notes", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="MultwarehouseSimulationRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("input_snapshot", models.JSONField(default=dict)),
                ("result_snapshot", models.JSONField(default=dict)),
                ("status", models.CharField(max_length=32)),
                ("quote_basis", models.CharField(max_length=64)),
                ("actor_label", models.CharField(default="local-operator", max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
