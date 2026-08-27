import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import ExternalChannelProductSnapshot, IntegrationReadStatus, MasterProduct, ProductVariant


@override_settings(DEBUG=True)
class ChannelRefreshApiTests(APITestCase):
    def test_get_exposes_read_only_idle_state(self):
        response = self.client.get("/api/catalogo/workspace/refresh-channels/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["external_writes"], 0)

    @patch("catalogo.views.start_channel_refresh")
    def test_post_starts_background_read_without_external_writes(self, starter):
        starter.return_value = ({
            "run_id": "qa-run", "status": "RUNNING", "channels": [], "external_writes": 0,
        }, True)
        response = self.client.post("/api/catalogo/workspace/refresh-channels/", {}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "RUNNING")
        self.assertEqual(response.data["external_writes"], 0)


class FalabellaRefreshCommandTests(TestCase):
    def test_valid_snapshot_is_imported_and_linked_by_shopify_sku(self):
        product = MasterProduct.objects.create(
            shopify_product_id="gid://shopify/Product/refresh-fal", title="Producto", status="ACTIVE",
        )
        ProductVariant.objects.create(
            product=product, shopify_variant_id="gid://shopify/ProductVariant/refresh-fal",
            sku="FAL-EXACT-1",
        )
        payload = {
            "channel": "FALABELLA", "complete": True,
            "observed_at": "2026-08-26T22:00:00Z",
            "source": "Falabella GetProducts read-only",
            "records": [{
                "external_product_id": "FAL-SHOP-1", "external_variant_id": "FAL-EXACT-1",
                "sku": "FAL-EXACT-1", "title": "Producto", "state": "active", "price": 99000,
            }],
        }
        with patch(
            "catalogo.management.commands.refresh_falabella_snapshot.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ):
            output = StringIO()
            call_command("refresh_falabella_snapshot", stdout=output)

        row = ExternalChannelProductSnapshot.objects.get(channel="FALABELLA")
        self.assertEqual(row.match_status, ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU)
        self.assertIn("externalWrites=0", output.getvalue())


class ShopifyRefreshCommandTests(TestCase):
    def test_incremental_empty_read_preserves_existing_master(self):
        product = MasterProduct.objects.create(
            shopify_product_id="gid://shopify/Product/existing", title="Existente", status="ACTIVE",
        )
        ProductVariant.objects.create(
            product=product, shopify_variant_id="gid://shopify/ProductVariant/existing", sku="KEEP-ME",
        )
        IntegrationReadStatus.objects.create(
            system="SHOPIFY", capability="marketplace_catalog_snapshot", status="AVAILABLE",
            message="Anterior", observed_at=timezone.now(), last_success_at=timezone.now(), external_writes=0,
        )
        payload = {
            "data": {"productVariants": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
            "pages": 1, "complete": False, "incremental": True, "externalWrites": 0,
        }
        with patch(
            "catalogo.management.commands.refresh_shopify_snapshot.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ):
            call_command("refresh_shopify_snapshot")
        product.refresh_from_db()
        self.assertEqual(product.status, "ACTIVE")
        self.assertTrue(ProductVariant.objects.filter(sku="KEEP-ME").exists())
