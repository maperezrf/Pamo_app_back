import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import (
    MasterProduct,
    ProductImage,
    ProductVariant,
    SodimacAuditTask,
    SodimacCatalogImportBatch,
    SodimacCatalogLink,
)
from .sodimac_catalog import (
    DEFAULT_HEADER_MAPPING,
    apply_sodimac_import,
    build_sodimac_workspace,
    enqueue_incremental_audits,
    preview_sodimac_import,
    reverse_sodimac_import,
)


def csv_bytes(rows):
    header = "sku_shopify,sku_sodimac,listing_id,url_sodimac,titulo_sodimac,marca_sodimac,descripcion_sodimac,imagenes_urls,atributos_json,estado_publicacion,inventario,fuente_inventario,proveedor,bodega,fecha_archivo,ultima_verificacion\n"
    return (header + "\n".join(rows) + "\n").encode()


class SodimacCatalogWorkflowTests(TestCase):
    def setUp(self):
        self.product = MasterProduct.objects.create(
            title="Taladro profesional 20V", vendor="Proveedor Uno", brand="Marca Uno",
            description_html="Taladro con batería", quality_score=80,
        )
        self.variant = ProductVariant.objects.create(product=self.product, sku="SHOP-001", title="Única")
        ProductImage.objects.create(product=self.product, source_url="https://cdn.example/image.jpg", position=1)
        self.row = "SHOP-001,SOD-900,LIST-900,https://example.invalid/list-900,Taladro profesional 20V,Marca Uno,Taladro con batería,https://cdn.example/image.jpg,{},PUBLICADO,3,CONFIRMED_BY_FILE,Proveedor Uno,Bodega Uno,2026-08-25,2026-08-25T08:00:00-05:00"

    def test_preview_apply_is_idempotent_and_reverse_is_local(self):
        content = csv_bytes([self.row])
        batch = preview_sodimac_import("sodimac.csv", content, DEFAULT_HEADER_MAPPING, "qa")
        self.assertEqual(batch.status, "PREVIEW")
        self.assertEqual(batch.valid_rows, 1)
        same = preview_sodimac_import("renamed.csv", content, DEFAULT_HEADER_MAPPING, "qa")
        self.assertEqual(same.id, batch.id)

        applied = apply_sodimac_import(batch.id, "qa")
        self.assertEqual(applied.status, "APPLIED_LOCAL")
        self.assertEqual(applied.applied_links, 1)
        self.assertEqual(SodimacCatalogLink.objects.count(), 1)
        apply_sodimac_import(batch.id, "qa")
        self.assertEqual(SodimacCatalogLink.objects.count(), 1)
        observation = SodimacCatalogLink.objects.get().observations.get()
        self.assertEqual(observation.inventory_source, "CONFIRMED_BY_FILE")
        self.assertEqual(observation.external_writes, 0)
        self.assertEqual(observation.field_comparison["images"]["visual_similarity"], "UNKNOWN")

        reversed_batch = reverse_sodimac_import(batch.id, "qa")
        self.assertEqual(reversed_batch.status, "REVERSED")
        link = SodimacCatalogLink.objects.get()
        self.assertFalse(link.active)
        self.assertEqual(link.status, "STALE")

    def test_ambiguous_sodimac_mapping_is_never_applied(self):
        other_product = MasterProduct.objects.create(title="Otro", vendor="Proveedor Uno")
        ProductVariant.objects.create(product=other_product, sku="SHOP-002")
        second = self.row.replace("SHOP-001", "SHOP-002").replace("LIST-900", "LIST-901")
        batch = preview_sodimac_import("ambiguous.csv", csv_bytes([self.row, second]), DEFAULT_HEADER_MAPPING, "qa")
        self.assertEqual(batch.status, "BLOCKED")
        self.assertEqual(batch.conflict_rows, 2)
        self.assertEqual(SodimacCatalogLink.objects.count(), 0)

    def test_manual_decision_is_preserved(self):
        first = preview_sodimac_import("first.csv", csv_bytes([self.row]), DEFAULT_HEADER_MAPPING, "qa")
        apply_sodimac_import(first.id, "qa")
        link = SodimacCatalogLink.objects.get()
        link.manual_decision = True
        link.status = "NEEDS_REVIEW"
        link.save(update_fields=["manual_decision", "status"])
        updated = self.row.replace("https://example.invalid/list-900", "https://example.invalid/changed")
        second = preview_sodimac_import("second.csv", csv_bytes([updated]), DEFAULT_HEADER_MAPPING, "qa")
        apply_sodimac_import(second.id, "qa")
        link.refresh_from_db()
        self.assertTrue(link.manual_decision)
        self.assertEqual(link.status, "NEEDS_REVIEW")
        self.assertEqual(SodimacCatalogLink.objects.count(), 1)

    def test_incremental_queue_creates_manual_fallback_without_network(self):
        batch = preview_sodimac_import("queue.csv", csv_bytes([self.row]), DEFAULT_HEADER_MAPPING, "qa")
        apply_sodimac_import(batch.id, "qa")
        observation = SodimacCatalogLink.objects.get().observations.get()
        observation.expires_at = timezone.now() - timedelta(minutes=1)
        observation.save(update_fields=["expires_at"])
        created = enqueue_incremental_audits("qa")
        self.assertEqual(created, 1)
        task = SodimacAuditTask.objects.filter(reason="PUBLIC_VERIFICATION_REQUIRES_APPROVED_MECHANISM").get()
        self.assertEqual(task.status, "MANUAL_REQUIRED")
        self.assertEqual(task.external_writes, 0)
        workspace = build_sodimac_workspace()
        self.assertEqual(workspace["daily_contract"]["scheduler"], "NOT_CONFIGURED")
        self.assertEqual(workspace["real_connectors"]["inventory"], "DISCONNECTED")


class SodimacCatalogAPITests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="sodimac-qa@test.invalid")
        self.client.force_login(user)

    def test_workspace_and_fixture_are_explicitly_local(self):
        response = self.client.get("/api/catalogo/sodimac/workspace/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["external_writes"], 0)
        fixture = self.client.post(
            "/api/catalogo/sodimac/workspace/",
            data=json.dumps({"action": "LOAD_DEMO_FIXTURE", "actor_label": "qa"}),
            content_type="application/json",
        )
        self.assertEqual(fixture.status_code, 200)
        self.assertTrue(fixture.json()["batch"]["is_fixture"])
        self.assertEqual(fixture.json()["external_writes"], 0)
        self.assertEqual(SodimacCatalogImportBatch.objects.get().source_filename, "sodimac_catalog_demo.csv")
