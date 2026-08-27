import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0005_logisticsquotesnapshot_external_reference_hash_and_more")]

    operations = [
        migrations.CreateModel(
            name="EnviaQuoteContractRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_fingerprint", models.CharField(max_length=64, unique=True)),
                ("request_snapshot", models.JSONField(default=dict)),
                ("response_snapshot", models.JSONField(default=dict)),
                ("status", models.CharField(max_length=24)),
                ("fixture_name", models.CharField(max_length=180)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="PhysicalEvidenceCandidate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("field", models.CharField(choices=[("WEIGHT", "Peso"), ("LENGTH", "Largo"), ("WIDTH", "Ancho"), ("HEIGHT", "Alto")], max_length=12)),
                ("scope", models.CharField(choices=[("PRODUCT", "Producto"), ("PACKAGE", "Empaque para transporte")], max_length=12)),
                ("classification", models.CharField(choices=[("CONFIRMED", "Confirmado"), ("DERIVED", "Derivado de evidencia exacta"), ("ESTIMATED", "Estimado por producto similar"), ("CONFLICT", "Conflicto entre fuentes")], max_length=16)),
                ("source_type", models.CharField(choices=[("SHOPIFY_STRUCTURED", "Shopify estructurado"), ("SHOPIFY_METAFIELD", "Metacampo Shopify"), ("SHOPIFY_DESCRIPTION", "Descripción Shopify"), ("PROVIDER_CATALOG", "Catálogo proveedor"), ("MANUFACTURER", "Fabricante oficial"), ("PUBLIC_RETAIL_EXACT", "Comercio público exacto"), ("PUBLIC_SIMILAR", "Producto público similar"), ("MANUAL", "Validación manual")], max_length=32)),
                ("source_url", models.URLField(blank=True, max_length=700)),
                ("source_reference", models.CharField(max_length=500)),
                ("matching_identifier_type", models.CharField(blank=True, max_length=40)),
                ("matching_identifier_value", models.CharField(blank=True, max_length=200)),
                ("evidence_excerpt", models.CharField(max_length=700)),
                ("evidence_selector", models.CharField(blank=True, max_length=300)),
                ("observed_at", models.DateTimeField()),
                ("extraction_method", models.CharField(max_length=80)),
                ("original_value", models.DecimalField(decimal_places=4, max_digits=14)),
                ("original_unit", models.CharField(max_length=16)),
                ("normalized_value", models.DecimalField(decimal_places=4, max_digits=14)),
                ("normalized_unit", models.CharField(max_length=16)),
                ("confidence", models.DecimalField(decimal_places=4, max_digits=5)),
                ("conflict", models.BooleanField(default=False)),
                ("conflict_details", models.JSONField(blank=True, default=dict)),
                ("content_fingerprint", models.CharField(max_length=64, unique=True)),
                ("stale_after", models.DateTimeField(blank=True, null=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("supplier_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="physical_candidates", to="catalogo.suppliercatalogitem")),
                ("variant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="physical_candidates", to="catalogo.productvariant")),
            ],
            options={"ordering": ["-confidence", "field", "-observed_at"]},
        ),
        migrations.CreateModel(
            name="PhysicalEvidenceDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("APPROVE_LOCAL", "Aprobar localmente"), ("REJECT", "Rechazar"), ("REQUEST_PROVIDER", "Pedir al proveedor")], max_length=24)),
                ("reason", models.CharField(max_length=500)),
                ("actor_label", models.CharField(default="local-operator", max_length=160)),
                ("decision_snapshot", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
                ("candidate", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="decisions", to="catalogo.physicalevidencecandidate")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PhysicalEnrichmentPilotSelection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveIntegerField()),
                ("criteria", models.JSONField(default=list)),
                ("rank", models.PositiveSmallIntegerField()),
                ("selected_at", models.DateTimeField(auto_now=True)),
                ("supplier_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pilot_selections", to="catalogo.suppliercatalogitem")),
                ("variant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="physical_pilot_selection", to="catalogo.productvariant")),
            ],
            options={"ordering": ["rank"]},
        ),
        migrations.CreateModel(
            name="ShopifyPhysicalUpdatePreview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("BLOCKED", "Bloqueado"), ("READY_LOCAL", "Listo para revisión local"), ("REJECTED", "Rechazado localmente")], default="BLOCKED", max_length=20)),
                ("previous_values", models.JSONField(default=dict)),
                ("proposed_metafields", models.JSONField(default=dict)),
                ("evidence_snapshot", models.JSONField(default=dict)),
                ("blockers", models.JSONField(default=list)),
                ("rollback_payload", models.JSONField(default=dict)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("external_writes", models.PositiveSmallIntegerField(default=0)),
                ("variant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="physical_update_preview", to="catalogo.productvariant")),
            ],
        ),
        migrations.AddIndex(
            model_name="physicalevidencecandidate",
            index=models.Index(fields=["variant", "scope", "classification"], name="catalogo_ph_variant_7b081b_idx"),
        ),
        migrations.AddIndex(
            model_name="physicalevidencecandidate",
            index=models.Index(fields=["supplier_item", "field"], name="catalogo_ph_supplie_642cfd_idx"),
        ),
    ]
