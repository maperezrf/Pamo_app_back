from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import (
    CanonicalCostSelection,
    CostObservation,
    InventorySourceSnapshot,
    MasterProduct,
    ProductVariant,
    SodimacCatalogLink,
    SodimacKit,
    SodimacKitComponent,
)
from .sodimac_catalog import apply_sodimac_import, preview_sodimac_import
from .sodimac_kits import build_sodimac_kit_workspace, import_sodimac_kits


def csv_bytes(header, rows):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


class SodimacPartialPublicationImportTests(TestCase):
    def test_valid_product_links_apply_while_unresolved_rows_stay_in_review(self):
        product = MasterProduct.objects.create(title="Producto existente")
        ProductVariant.objects.create(product=product, sku="PAMO-OK")
        content = csv_bytes(
            "sku_sodimac,sku_pamo,ean",
            ["7001,PAMO-OK,770000000001", "7002,PAMO-MISSING,770000000002"],
        )
        mapping = {
            "canonical_sku": "sku_pamo",
            "sodimac_sku": "sku_sodimac",
            "listing_id": "sku_sodimac",
            "barcode": "ean",
        }

        batch = preview_sodimac_import(
            "productos_sodimac.csv", content, mapping, "qa", allow_partial=True,
        )
        self.assertEqual(batch.status, "PREVIEW_PARTIAL")
        self.assertEqual(batch.valid_rows, 1)
        self.assertEqual(batch.rejected_rows, 1)

        applied = apply_sodimac_import(batch.id, "qa")
        self.assertEqual(applied.status, "APPLIED_PARTIAL")
        self.assertEqual(applied.applied_links, 1)
        self.assertTrue(SodimacCatalogLink.objects.filter(canonical_sku="PAMO-OK", sodimac_sku="7001").exists())


class SodimacKitRecipeTests(TestCase):
    def setUp(self):
        first_product = MasterProduct.objects.create(title="Componente A")
        second_product = MasterProduct.objects.create(title="Componente B")
        self.first = ProductVariant.objects.create(product=first_product, sku="COMP-A", price=100)
        self.second = ProductVariant.objects.create(product=second_product, sku="COMP-B", price=50)
        for variant, cost, stock in ((self.first, 40, 10), (self.second, 20, 5)):
            observation = CostObservation.objects.create(
                variant=variant, source="MANUAL", raw_cost=cost, derived_net_cost=cost,
                tax_treatment="INCLUDED", observed_at=timezone.now(), evidence_reference="qa",
            )
            CanonicalCostSelection.objects.create(
                variant=variant, observation=observation, policy_name="QA", reason="Costo de prueba",
            )
            InventorySourceSnapshot.objects.create(
                variant=variant, source_name="QA", reported_stock=stock, reserved_stock=0,
                safety_stock=0, available_to_promise=stock, stock_unknown=False,
                observed_at=timezone.now(), update_method="FILE", canonical=True,
            )

    def test_recipe_aggregates_repeated_components_and_derives_economics(self):
        content = csv_bytes(
            "kitnumber,ean,sku,quantity",
            ["KIT-1,770000000010,COMP-A,2", "KIT-1,770000000010,COMP-A,1", "KIT-1,770000000010,COMP-B,1"],
        )

        batch = import_sodimac_kits("kits.csv", content, {}, "qa")
        kit = SodimacKit.objects.get(sodimac_kit_sku="KIT-1")
        self.assertEqual(batch.kit_count, 1)
        self.assertEqual(kit.status, SodimacKit.Status.RESOLVED)
        self.assertEqual(kit.components.get(component_sku="COMP-A").quantity, 3)

        serialized = build_sodimac_kit_workspace()["kits"][0]
        self.assertEqual(serialized["economics"]["component_cost_total"], Decimal("140"))
        self.assertEqual(serialized["economics"]["component_reference_price_total"], Decimal("350"))
        self.assertEqual(serialized["economics"]["possible_kit_units"], 3)

    def test_unresolved_component_blocks_totals_instead_of_becoming_zero(self):
        content = csv_bytes(
            "kitnumber,ean,sku,quantity",
            ["KIT-2,,COMP-A,1", "KIT-2,,COMP-MISSING,2"],
        )

        import_sodimac_kits("kits-missing.csv", content, {}, "qa")
        kit = SodimacKit.objects.get(active=True, sodimac_kit_sku="KIT-2")
        self.assertEqual(kit.status, SodimacKit.Status.PARTIAL)
        self.assertEqual(
            kit.components.get(component_sku="COMP-MISSING").match_status,
            SodimacKitComponent.MatchStatus.MISSING_SHOPIFY,
        )
        serialized = build_sodimac_kit_workspace()["kits"][0]
        self.assertIsNone(serialized["economics"]["component_cost_total"])
        self.assertIsNone(serialized["economics"]["component_reference_price_total"])
        self.assertIsNone(serialized["economics"]["possible_kit_units"])
