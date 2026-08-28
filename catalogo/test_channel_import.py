from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .channel_import import ChannelImportError, import_external_channel_snapshot
from .models import (
    ChannelSnapshot,
    ExternalChannelProductSnapshot,
    IntegrationReadStatus,
    MasterProduct,
    ProductImage,
    ProductVariant,
)


class ExternalChannelImportTests(TestCase):
    def setUp(self):
        self.product = MasterProduct.objects.create(
            shopify_product_id="gid://shopify/Product/1",
            title="Lavamanos Pamo",
            status="ACTIVE",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            shopify_variant_id="gid://shopify/ProductVariant/1",
            sku="SKU-001",
            price="100000",
        )

    def test_complete_import_links_only_unique_exact_sku_and_preserves_missing(self):
        result = import_external_channel_snapshot("FALABELLA", [
            {"external_product_id": "fal-1", "sku": "SKU-001", "title": "Exacto", "price": 120000, "inventory": 4},
            {"external_product_id": "fal-2", "sku": "SKU-FALTA", "title": "Faltante", "price": 90000},
        ], complete=True, source="Falabella GetProducts read-only")

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["exact"], 1)
        self.assertEqual(result["missing_shopify"], 1)
        self.assertEqual(ChannelSnapshot.objects.filter(channel="FALABELLA").count(), 1)
        self.assertEqual(
            ExternalChannelProductSnapshot.objects.get(external_product_id="fal-2").match_status,
            "MISSING_SHOPIFY",
        )
        status = IntegrationReadStatus.objects.get(system="FALABELLA", capability="marketplace_catalog_snapshot")
        self.assertEqual(status.status, "AVAILABLE")
        self.assertEqual(status.external_writes, 0)

    def test_duplicate_channel_sku_is_not_selected_as_authoritative_listing(self):
        result = import_external_channel_snapshot("MERCADO_LIBRE", [
            {"external_product_id": "MCO1", "sku": "SKU-001", "title": "Uno"},
            {"external_product_id": "MCO2", "sku": "sku-001", "title": "Dos"},
        ], complete=True)
        self.assertEqual(result["duplicates"], 2)
        self.assertEqual(result["linked_master_rows"], 0)
        self.assertFalse(ChannelSnapshot.objects.filter(channel="MERCADO_LIBRE").exists())

    def test_empty_complete_snapshot_fails_closed_and_does_not_stale_previous_rows(self):
        import_external_channel_snapshot("FALABELLA", [
            {"external_product_id": "fal-1", "sku": "SKU-001", "title": "Exacto"},
        ], complete=True)
        with self.assertRaises(ChannelImportError):
            import_external_channel_snapshot("FALABELLA", [], complete=True)
        self.assertTrue(ExternalChannelProductSnapshot.objects.get(external_product_id="fal-1").active)

    def test_complete_refresh_marks_rows_missing_from_new_read_as_stale(self):
        import_external_channel_snapshot("FALABELLA", [
            {"external_product_id": "fal-1", "sku": "SKU-001"},
            {"external_product_id": "fal-2", "sku": "SKU-002"},
        ], complete=True)
        import_external_channel_snapshot("FALABELLA", [
            {"external_product_id": "fal-1", "sku": "SKU-001"},
        ], complete=True)
        stale = ExternalChannelProductSnapshot.objects.get(external_product_id="fal-2")
        self.assertFalse(stale.active)
        self.assertEqual(stale.match_status, "STALE")

    def test_madecentro_pilot_is_local_snapshot_with_commercial_payload(self):
        result = import_external_channel_snapshot("MADECENTRO", [{
            "external_product_id": "MADECENTRO-PILOT:SKU-001",
            "sku": "SKU-001",
            "title": "Lavamanos piloto",
            "price": 80000,
            "state": "PILOT_MARGIN_PENDING",
            "payload": {
                "classification": "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL",
                "public_suggested_price": "100000",
            },
        }], complete=True, source="XLSX local")

        self.assertEqual(result["exact"], 1)
        snapshot = ExternalChannelProductSnapshot.objects.get(channel="MADECENTRO")
        self.assertEqual(snapshot.payload["classification"], "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL")
        self.assertEqual(ChannelSnapshot.objects.filter(channel="MADECENTRO").count(), 1)


class ChannelAlignmentApiTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="alignment-local@test.invalid"))
        product = MasterProduct.objects.create(
            shopify_product_id="gid://shopify/Product/2", title="Grifería", status="ACTIVE",
        )
        ProductImage.objects.create(product=product, source_url="https://cdn.example.com/thumb.jpg", position=1)
        ProductVariant.objects.create(
            product=product, shopify_variant_id="gid://shopify/ProductVariant/2", sku="GR-1", price="50000",
        )
        import_external_channel_snapshot("FALABELLA", [
            {"external_product_id": "fal-gr-1", "sku": "GR-1", "title": "Grifería", "image_url": "https://cdn.example.com/fal.jpg"},
        ], observed_at=timezone.now(), complete=True)

    def test_alignment_endpoint_reports_counts_and_thumbnail(self):
        response = self.client.get("/api/catalogo/alignment/?channel=FALABELLA")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["FALABELLA"]["exact"], 1)
        self.assertEqual(response.json()["records"][0]["image_url"], "https://cdn.example.com/fal.jpg")
        self.assertEqual(response.json()["external_writes"], 0)

    def test_alignment_endpoint_exposes_madecentro_pilot_context(self):
        import_external_channel_snapshot("MADECENTRO", [{
            "external_product_id": "MADECENTRO-PILOT:GR-1",
            "sku": "GR-1",
            "title": "Grifería piloto",
            "price": 40000,
            "payload": {
                "classification": "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL",
                "public_suggested_price": "50000",
                "margin_warning": "Margen por validar.",
            },
        }], complete=True)
        response = self.client.get("/api/catalogo/alignment/?channel=MADECENTRO")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["MADECENTRO"]["exact"], 1)
        self.assertEqual(response.json()["channel_context"]["classification"], "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL")
        self.assertEqual(response.json()["records"][0]["payload"]["public_suggested_price"], "50000")

    def test_workspace_serializes_shopify_thumbnail(self):
        response = self.client.get("/api/catalogo/workspace/?page=1&page_size=50")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["products"][0]["images"][0]["source_url"], "https://cdn.example.com/thumb.jpg")
