from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ChannelSnapshot,
    InventoryLevel,
    InventorySourceSnapshot,
    MasterProduct,
    ProductVariant,
    ProviderConfig,
    ShopifySyncItem,
    ShopifySyncPolicy,
)
from .shopify_sync import (
    ShopifySyncError,
    _verify_shopify_result,
    build_sync_proposal,
    execute_shopify_pilot,
    scan_shopify_sync,
)


class ShopifySyncProposalTests(TestCase):
    def setUp(self):
        self.product = MasterProduct.objects.create(
            shopify_product_id="gid://shopify/Product/1",
            title="Producto QA",
            vendor="TAUMM",
            status="ACTIVE",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="QA-001",
            shopify_variant_id="gid://shopify/ProductVariant/1",
            price=Decimal("100000"),
        )
        self.snapshot = ChannelSnapshot.objects.create(
            product=self.product,
            variant=self.variant,
            channel="SHOPIFY",
            external_product_id=self.product.shopify_product_id,
            external_variant_id=self.variant.shopify_variant_id,
            state="ACTIVE",
            price=Decimal("100000"),
            cost=Decimal("30000"),
            payload={"inventoryItemId": "gid://shopify/InventoryItem/1"},
            observed_at=timezone.now(),
        )
        self.policy = ShopifySyncPolicy.objects.create(
            key="PRIMARY",
            environment="BETA",
            scan_enabled=True,
            writes_enabled=False,
            price_enabled=True,
            inventory_enabled=True,
            maximum_batch_size=5,
            source_max_age_minutes=360,
        )

    def test_price_can_be_ready_while_inventory_is_blocked(self):
        proposal = build_sync_proposal(self.variant, self.policy)
        self.assertEqual(proposal["status"], ShopifySyncItem.Status.READY)
        self.assertIn("PRICE", proposal["fields"])
        self.assertIn("INVENTORY_SOURCE_MISSING", proposal["blockers"])
        self.assertNotEqual(proposal["proposed"]["price"], proposal["previous"]["price"])

    def test_inventory_uses_non_shopify_canonical_source_and_exact_location(self):
        provider = ProviderConfig.objects.create(name="TAUMM")
        InventorySourceSnapshot.objects.create(
            variant=self.variant,
            provider=provider,
            source_name="TAUMM",
            warehouse_name="Taumm",
            reported_stock=Decimal("8"),
            available_to_promise=Decimal("8"),
            stock_unknown=False,
            observed_at=timezone.now(),
            canonical=True,
            update_method="API",
            evidence_reference="Proveedor QA",
        )
        InventoryLevel.objects.create(
            variant=self.variant,
            location_external_id="gid://shopify/Location/1",
            location_name="Taumm",
            available=Decimal("3"),
            observed_at=timezone.now(),
        )
        proposal = build_sync_proposal(self.variant, self.policy)
        self.assertIn("INVENTORY", proposal["fields"])
        self.assertEqual(proposal["previous"]["inventory"], 3)
        self.assertEqual(proposal["proposed"]["inventory"], 8)
        self.assertEqual(proposal["proposed"]["location_id"], "gid://shopify/Location/1")

    def test_stale_inventory_never_becomes_a_write(self):
        provider = ProviderConfig.objects.create(name="TAUMM")
        InventorySourceSnapshot.objects.create(
            variant=self.variant,
            provider=provider,
            source_name="TAUMM",
            warehouse_name="Taumm",
            reported_stock=Decimal("8"),
            available_to_promise=Decimal("8"),
            stock_unknown=False,
            observed_at=timezone.now() - timedelta(days=2),
            canonical=True,
            update_method="API",
            evidence_reference="Fuente vencida",
        )
        proposal = build_sync_proposal(self.variant, self.policy)
        self.assertNotIn("INVENTORY", proposal["fields"])
        self.assertIn("INVENTORY_SOURCE_STALE", proposal["blockers"])

    def test_preview_is_auditable_and_has_zero_external_writes(self):
        run = scan_shopify_sync(skus=["QA-001"], trigger="QA")
        self.assertEqual(run.scanned_count, 1)
        self.assertEqual(run.external_writes, 0)
        self.assertEqual(run.items.count(), 1)

    def test_execute_is_fail_closed_until_all_write_gates_are_enabled(self):
        run = scan_shopify_sync(skus=["QA-001"], trigger="QA")
        self.policy.allowlisted_skus = ["QA-001"]
        self.policy.save()
        with self.assertRaises(ShopifySyncError) as caught:
            execute_shopify_pilot(run_id=run.id, skus=["QA-001"], confirmation="SHOPIFY_BETA_SYNC")
        self.assertEqual(caught.exception.code, "WRITE_GATES_DISABLED")

    def test_reread_must_confirm_price_exactly(self):
        run = scan_shopify_sync(skus=["QA-001"], trigger="QA")
        item = run.items.get()
        with self.assertRaises(ShopifySyncError) as caught:
            _verify_shopify_result(item, {
                "id": self.variant.shopify_variant_id,
                "price": "1",
                "inventoryItem": {"inventoryLevels": {"nodes": []}},
            })
        self.assertEqual(caught.exception.code, "SHOPIFY_PRICE_VERIFY_MISMATCH")

    def test_reread_confirms_exact_inventory_location_and_quantity(self):
        provider = ProviderConfig.objects.create(name="TAUMM")
        InventorySourceSnapshot.objects.create(
            variant=self.variant,
            provider=provider,
            source_name="TAUMM",
            warehouse_name="Taumm",
            reported_stock=Decimal("8"),
            available_to_promise=Decimal("8"),
            stock_unknown=False,
            observed_at=timezone.now(),
            canonical=True,
            update_method="API",
            evidence_reference="Proveedor QA",
        )
        InventoryLevel.objects.create(
            variant=self.variant,
            location_external_id="gid://shopify/Location/1",
            location_name="Taumm",
            available=Decimal("3"),
            observed_at=timezone.now(),
        )
        run = scan_shopify_sync(skus=["QA-001"], trigger="QA")
        item = run.items.get()
        _verify_shopify_result(item, {
            "id": self.variant.shopify_variant_id,
            "price": item.proposed_values["price"],
            "inventoryItem": {"inventoryLevels": {"nodes": [{
                "location": {"id": "gid://shopify/Location/1", "name": "Taumm"},
                "quantities": [{"name": "available", "quantity": 8}],
            }]}},
        })


class ShopifySyncApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="shopify-sync-qa", email="shopify-sync-qa@pamo.local", password="qa-pass"
        )
        self.client.force_authenticate(self.user)

    def test_workspace_starts_in_beta_with_writes_disabled(self):
        response = self.client.get("/api/catalogo/shopify/sync-workspace/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["environment"], "BETA")
        self.assertFalse(response.data["gates"]["execution_allowed"])

    def test_ui_policy_cannot_enable_external_writes(self):
        response = self.client.post(
            "/api/catalogo/shopify/sync-workspace/",
            {"action": "UPDATE_POLICY", "writes_enabled": True, "maximum_batch_size": 25},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["policy"]["writes_enabled"])
