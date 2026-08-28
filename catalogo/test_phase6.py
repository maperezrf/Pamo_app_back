from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.utils import timezone as django_timezone

from .models import CanonicalCostSelection, CatalogHistoryEvent, CostObservation, MasterProduct, PricingPolicy, ProductVariant, ProviderConfig
from .phase6 import demo_case, preview_pricing, resolve_rule, rule_match, simulate_multwarehouse_cart
from .phase6_services import apply_pricing_batch, create_pricing_preview, reverse_pricing_batch, save_rule


NOW = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)


def rule(rule_id="global", **changes):
    payload = {
        "id": rule_id, "name": rule_id, "active": True, "priority": 10,
        "filters": {},
        "pricing": {
            "mode": "MARKUP_PERCENT", "value": 30, "minimum_margin_percent": 20,
            "channel_commission_percent": 10, "payment_percent": 3,
            "channel_fixed_charge": 0, "payment_fixed_charge": 0,
            "rounding_increment": 500, "reserve_cap": 20000,
        },
    }
    for key, value in changes.items():
        if key == "pricing":
            payload["pricing"].update(value)
        else:
            payload[key] = value
    return payload


def context(**changes):
    payload = {
        "sku": "SKU-1", "before_price": 150000, "canonical_cost": 100000,
        "cost_source": "Barú PDF", "tax_treatment": "INCLUDED", "tax_rate": 19,
        "shipping_subsidy_used": 5000, "provider": ["Barú"], "brand": ["Pamo"],
        "collection": ["Baños"], "category": ["Lavamanos"], "product_type": ["Sanitario"],
        "tags": ["nuevo", "premium"], "channel": ["SHOPIFY"], "warehouse": ["Bogotá"],
        "inventory": 5,
    }
    payload.update(changes)
    return payload


class Phase6PricingEngineTests(TestCase):
    def test_markup_fixed_and_gross_margin_are_distinct(self):
        rows = []
        for mode, value in (("MARKUP_PERCENT", 25), ("FIXED_INCREMENT", 25000), ("GROSS_MARGIN", 25)):
            current = rule(mode, pricing={"mode": mode, "value": value, "minimum_margin_percent": 0, "channel_commission_percent": 0, "payment_percent": 0})
            rows.append(preview_pricing([context(shipping_subsidy_used=0)], [current])["rows"][0]["candidate_price"])
        self.assertEqual(rows[0], Decimal("125000.00"))
        self.assertEqual(rows[1], Decimal("125000.00"))
        self.assertEqual(rows[2], Decimal("133333.33"))
        self.assertNotEqual(rows[0], rows[2])

    def test_baru_tax_included_is_not_applied_twice(self):
        row = preview_pricing([context(shipping_subsidy_used=0)], [rule()])["rows"][0]
        self.assertEqual(row["normalized_cost_iva_included"], Decimal("100000.00"))

    def test_protected_floor_rounding_and_reserve_cap(self):
        row = preview_pricing([context()], [rule()])["rows"][0]
        self.assertEqual(row["candidate_price"], Decimal("130000.00"))
        self.assertEqual(row["protected_floor"], Decimal("156716.42"))
        self.assertEqual(row["final_price"], Decimal("157000.00"))
        self.assertEqual(row["reserve_added_to_price"], Decimal("0.00"))
        self.assertGreaterEqual(row["achieved_margin_percent"], Decimal("20"))

    def test_configurable_channel_expenses_are_separated_and_protect_margin(self):
        configured = rule(pricing={
            "minimum_margin_percent": 10,
            "channel_commission_percent": 12,
            "payment_percent": 3,
            "administrative_percent": 2,
            "administrative_fixed_charge": 1000,
            "logistics_percent": 1,
            "logistics_fixed_charge": 2000,
            "additional_costs": [
                {"label": "Publicidad", "basis": "PERCENT_SALE", "value": 4},
                {"label": "Empaque", "basis": "PERCENT_COST", "value": 2},
                {"label": "Operación", "basis": "FIXED", "value": 1500},
            ],
        })
        row = preview_pricing([context(shipping_subsidy_used=5000)], [configured])["rows"][0]
        self.assertGreater(row["protected_floor"], Decimal("100000"))
        self.assertGreater(row["administrative_amount"], Decimal("1000"))
        self.assertGreater(row["logistics_amount"], Decimal("2000"))
        self.assertGreater(row["additional_cost_amount"], Decimal("3500"))
        self.assertEqual([item["label"] for item in row["additional_cost_breakdown"]], ["Publicidad", "Empaque", "Operación"])
        self.assertGreaterEqual(row["achieved_margin_percent"], Decimal("10"))

    def test_subsidy_over_reserve_cap_blocks_instead_of_hiding_cost(self):
        row = preview_pricing([context(shipping_subsidy_used=25000)], [rule()])["rows"][0]
        self.assertIn("SUBSIDY_EXCEEDS_RESERVE_CAP", row["blockers"])
        self.assertIsNone(row["final_price"])

    def test_unknown_values_and_pending_exception_block(self):
        unknown = preview_pricing([context(canonical_cost=None)], [rule()])["rows"][0]
        pending = preview_pricing([context()], [rule(exception={"status": "PENDING"})])["rows"][0]
        self.assertIn("COST_UNKNOWN", unknown["blockers"])
        self.assertIn("EXCEPTION_PENDING", pending["blockers"])

    def test_filters_are_and_across_dimensions_or_inside_selection(self):
        filtered = rule(filters={"provider": ["Barú", "Otro"], "channel": ["SHOPIFY", "RAPPI"], "tags": ["premium"]})
        self.assertTrue(rule_match(filtered, context(), on_date=date(2026, 8, 25))[0])
        self.assertFalse(rule_match(filtered, context(channel=["MERCADO_LIBRE"]), on_date=date(2026, 8, 25))[0])

    def test_precedence_is_sku_then_channel_warehouse_then_attribute_then_global(self):
        rules = [
            rule("global", priority=999),
            rule("attribute", filters={"provider": ["Barú"]}, priority=900),
            rule("channel-warehouse", filters={"channel": ["SHOPIFY"], "warehouse": ["Bogotá"]}, priority=800),
            rule("sku", filters={"sku": ["SKU-1"]}, priority=1),
        ]
        resolved = resolve_rule(rules, context(), on_date=date(2026, 8, 25))
        self.assertEqual(resolved["winner"]["id"], "sku")

    def test_conflict_and_colombia_validity_are_explicit(self):
        rules = [rule("a", priority=10), rule("b", priority=10, pricing={"value": 40})]
        resolved = resolve_rule(rules, context(), on_date=date(2026, 8, 25))
        self.assertTrue(resolved["blocked"])
        self.assertEqual(len(resolved["conflicts"]), 2)
        future = rule("future", valid_from="2026-08-26")
        self.assertFalse(rule_match(future, context(), on_date=date(2026, 8, 25))[0])

    def test_maximum_below_floor_blocks(self):
        row = preview_pricing([context()], [rule(pricing={"maximum_price": 150000})])["rows"][0]
        self.assertIn("MAXIMUM_PRICE_BELOW_PROTECTED_RESULT", row["blockers"])

    def test_no_results_is_distinct_from_a_service_failure(self):
        preview = preview_pricing([], [rule()])
        self.assertEqual(preview["status"], "NO_RESULTS")
        self.assertEqual(preview["summary"], {"total": 0, "ready": 0, "blocked": 0})


class Phase6MultwarehouseEngineTests(TestCase):
    def test_required_demo_cases(self):
        one = demo_case("ONE_ORIGIN", now=NOW)
        multiple = demo_case("MULTIPLE_ORIGINS", now=NOW)
        insufficient = demo_case("INSUFFICIENT_STOCK", now=NOW)
        unknown = demo_case("UNKNOWN_WAREHOUSE", now=NOW)
        not_quotable = demo_case("NOT_QUOTABLE", now=NOW)
        margin = demo_case("SHIPPING_BREAKS_MARGIN", now=NOW)
        tie = demo_case("TIE", now=NOW)
        self.assertEqual(one["strategies"][0]["guide_count"], 1)
        self.assertEqual(multiple["strategies"][0]["guide_count"], 2)
        self.assertIn("INSUFFICIENT_VERIFIED_STOCK:DEMO-C", insufficient["blockers"])
        self.assertIn("WAREHOUSE_INVENTORY_UNKNOWN:DEMO-D", unknown["blockers"])
        self.assertIn("NO_COTIZABLE_PACKAGE:DEMO-E", not_quotable["blockers"])
        self.assertIn("SHIPPING_BREAKS_MINIMUM_MARGIN", margin["blockers"])
        self.assertEqual(tie["algorithm"], "EXACT_CARTESIAN_SMALL_CART")
        self.assertEqual(len(tie["strategies"][0]["assignment"]), 1)

    def test_sku_is_indivisible_and_demo_never_commercially_eligible(self):
        result = demo_case("TIE", now=NOW)
        self.assertFalse(result["sku_splitting"])
        self.assertFalse(result["commercially_eligible"])
        self.assertTrue(all(len(strategy["assignment"]) == 1 for strategy in result["strategies"]))

    def test_duplicate_cart_lines_are_consolidated_before_stock_assignment(self):
        inventory = [{"origin": "A", "available": 1, "unknown": False, "observed_at": NOW.isoformat(), "freshness_minutes": 1440}]
        line = {"sku": "SAME", "quantity": 1, "unit_price": 100000, "unit_cost": 50000, "package_ready": True, "inventory": inventory}
        result = simulate_multwarehouse_cart([line, dict(line)], demo_fixture=True, now=NOW)
        self.assertEqual(result["status"], "NO_COTIZABLE")
        self.assertIn("INSUFFICIENT_VERIFIED_STOCK:SAME", result["blockers"])

    def test_stale_inventory_is_not_stock(self):
        stale = NOW - timedelta(days=3)
        result = simulate_multwarehouse_cart([{
            "sku": "STALE", "quantity": 1, "unit_price": 100000, "unit_cost": 50000,
            "package_ready": True,
            "inventory": [{"origin": "Bogotá", "available": 3, "unknown": False, "observed_at": stale.isoformat(), "freshness_minutes": 60}],
        }], demo_fixture=True, now=NOW)
        self.assertEqual(result["status"], "NO_COTIZABLE")
        self.assertIn("STALE_INVENTORY:STALE", result["blockers"])

    def test_large_cart_uses_documented_deterministic_heuristic(self):
        inventory = [{"origin": "A", "available": 99, "unknown": False, "observed_at": NOW.isoformat(), "freshness_minutes": 1440}]
        lines = [{"sku": f"SKU-{index}", "quantity": 1, "unit_price": 100000, "unit_cost": 50000, "package_ready": True, "inventory": inventory} for index in range(9)]
        result = simulate_multwarehouse_cart(lines, demo_fixture=True, now=NOW)
        self.assertEqual(result["algorithm"], "DETERMINISTIC_FIRST_FEASIBLE_LARGE_CART")
        self.assertIn("carritos grandes", result["algorithm_note"])


class Phase6LocalPersistenceTests(DjangoTestCase):
    def setUp(self):
        provider = ProviderConfig.objects.create(name="Barú", tax_treatment="INCLUDED", tax_rate=19)
        product = MasterProduct.objects.create(title="Producto local", vendor="Barú", brand="Barú")
        self.variant = ProductVariant.objects.create(product=product, sku="LOCAL-1", price=150000)
        observation = CostObservation.objects.create(
            variant=self.variant, source="PROVIDER_CATALOG", provider=provider, raw_cost=100000,
            tax_treatment="INCLUDED", tax_rate=19, observed_at=django_timezone.now(), evidence_reference="fixture-test-local",
        )
        CanonicalCostSelection.objects.create(variant=self.variant, observation=observation, policy_name="test", reason="QA Fase 6")
        self.client.force_login(get_user_model().objects.create_user(username="phase6-local@test.invalid"))

    def test_preview_apply_idempotency_and_reverse_only_touch_local_price(self):
        draft = rule("local", filters={"sku": ["LOCAL-1"]}, pricing={"shipping_subsidy_used": 0})
        batch, preview = create_pricing_preview(
            {"sku": ["LOCAL-1"], "shipping_subsidy_used": 0}, draft_rule=draft,
            actor_label="qa-local", include_saved=False,
        )
        self.assertEqual(preview["status"], "READY_LOCAL")
        self.assertEqual(batch.external_writes, 0)
        applied = apply_pricing_batch(batch.id, "qa-local")
        reapplied = apply_pricing_batch(batch.id, "qa-local")
        self.variant.refresh_from_db()
        self.assertEqual(applied.id, reapplied.id)
        self.assertEqual(self.variant.price, preview["rows"][0]["final_price"])
        reversed_batch = reverse_pricing_batch(batch.id, "qa-local")
        self.variant.refresh_from_db()
        self.assertEqual(reversed_batch.status, "REVERSED")
        self.assertEqual(self.variant.price, Decimal("150000.00"))
        self.assertEqual(CatalogHistoryEvent.objects.filter(entity_id=str(self.variant.id)).count(), 2)

    def test_local_phase6_endpoints_expose_safety_contract(self):
        workspace = self.client.get("/api/catalogo/phase6/workspace/")
        demo = self.client.post(
            "/api/catalogo/phase6/multwarehouse/",
            data={"demo_case": "ONE_ORIGIN", "actor_label": "qa-local"},
            content_type="application/json",
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["safety"]["database"], "SQLite local")
        self.assertEqual(workspace.json()["safety"]["external_writes"], 0)
        self.assertEqual(demo.status_code, 200)
        self.assertFalse(demo.json()["current_rate"])
        self.assertFalse(demo.json()["guide_creation_allowed"])

    def test_drive_rule_source_is_preserved_as_auditable_hypothesis(self):
        payload = rule("drive-shopify", filters={"channel": ["SHOPIFY"]})
        payload.pop("id", None)
        payload["source"] = {
            "type": "GOOGLE_SHEETS_REVIEWED",
            "spreadsheet_id": "sheet-id",
            "sheet": "SHOPIFY",
            "reviewed_at": "2026-08-26",
        }
        saved = save_rule(payload)
        policy = PricingPolicy.objects.get(pk=saved["id"])
        self.assertEqual(saved["source"]["sheet"], "SHOPIFY")
        self.assertEqual(policy.combination["source"]["spreadsheet_id"], "sheet-id")
        self.assertEqual(policy.approval_status, PricingPolicy.ApprovalStatus.HYPOTHESIS)
        self.assertTrue(policy.simulation_only)

    def test_bulk_markup_scope_can_preview_more_than_250_variants(self):
        product = MasterProduct.objects.create(
            title="Colección masiva", vendor="Barú", brand="Marca masiva",
            collections=["Colección masiva"], category="Baños", product_type="Accesorio",
        )
        ProductVariant.objects.bulk_create([
            ProductVariant(
                product=product, sku=f"BULK-{index:03d}", price=150000,
                provider_cost=100000,
            )
            for index in range(260)
        ])
        draft = rule(
            "bulk-local",
            filters={"brand": ["Marca masiva"]},
            pricing={"shipping_subsidy_used": 0},
        )
        batch, preview = create_pricing_preview(
            {"brand": ["Marca masiva"], "limit": 5000, "shipping_subsidy_used": 0},
            draft_rule=draft, actor_label="qa-local", include_saved=False,
        )
        self.assertEqual(preview["summary"]["total"], 260)
        self.assertEqual(preview["summary"]["ready"], 260)
        self.assertEqual(batch.external_writes, 0)
