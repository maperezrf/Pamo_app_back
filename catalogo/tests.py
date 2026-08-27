from decimal import Decimal

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .baru_pdf import derive_net_cost, normalize_thousands_price
from .models import PricingPolicy, ProviderConfig
from .pricing import PricingInputError, calculate_price, commercial_sensitivity, normalize_provider_cost, select_policy, shipping_options
from .inventory import allocate_channels, calculate_available_to_promise
from .envia_quote import EnviaQuoteContractError, run_fixture_quote, validate_quote_request, validate_quote_response
from .physical import PhysicalValidationError, analyze_local_item, build_shopify_preview, description_measurements, normalize_measurement, select_pilot, upsert_candidate
from .physical_measurements import PhysicalMeasurementImportError, apply_measurement_import, preview_measurement_import, reverse_measurement_import
from .models import BulkSimulationRun, CatalogHistoryEvent, ChannelSnapshot, CostObservation, EnviaQuoteContractRun, IntegrationReadStatus, InventoryLevel, InventorySourceSnapshot, LogisticsQuoteSnapshot, MasterProduct, PhysicalEvidenceCandidate, PhysicalEvidenceDecision, PhysicalEnrichmentPilotSelection, PhysicalMeasurementImportBatch, PhysicalMeasurementImportRow, PhysicalMeasurementTask, ProductMetafield, ProductVariant, ProviderDataImport, ShopifyPhysicalUpdatePreview, SiigoProductSnapshot, SkuReconciliation, SupplierCatalogItem, SupplierItemInventorySnapshot


class PricingEngineTests(TestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(
            name="QA",
            tax_treatment=ProviderConfig.TaxTreatment.INCLUDED,
            tax_rate=19,
            rounding_increment=100,
        )
        self.general = PricingPolicy.objects.create(
            name="General", precedence="GENERAL", priority=100, channel="SHOPIFY", provider=self.provider,
            target_margin_percent=30, channel_commission_percent=2, logistics_reserve=3500,
            max_shipping_subsidy=12000, rounding_increment=100,
        )

    def test_margin_is_measured_over_sale_and_formula_is_auditable(self):
        result = calculate_price(
            provider=self.provider, supplier_price=Decimal("100000"), policy=self.general,
            quoted_shipping=Decimal("10000"), customer_shipping_charge=Decimal("3000"),
        )
        self.assertGreaterEqual(result.achieved_margin_percent, Decimal("30"))
        self.assertEqual(result.shipping_subsidy, Decimal("7000"))
        self.assertIn("plain_language", result.formula)

    def test_pending_tax_stops_calculation(self):
        self.provider.tax_treatment = ProviderConfig.TaxTreatment.PENDING
        self.provider.save()
        with self.assertRaises(PricingInputError):
            calculate_price(provider=self.provider, supplier_price=100000, policy=self.general)

    def test_included_tax_is_never_added_twice(self):
        self.assertEqual(normalize_provider_cost(self.provider, Decimal("119000")), Decimal("119000"))
        result = calculate_price(provider=self.provider, supplier_price=Decimal("119000"), policy=self.general)
        self.assertEqual(result.formula["tax_adjustment"], "0")
        self.assertTrue(result.formula["double_tax_guard"])

    def test_logistics_reserve_is_a_cap_and_not_an_automatic_surcharge(self):
        result = calculate_price(provider=self.provider, supplier_price=100000, policy=self.general)
        self.assertEqual(result.formula["reserve_behavior"], "CAP")
        self.assertEqual(result.formula["logistics_reserve_applied"], "0")
        self.assertEqual(result.formula["logistics_reserve_cap"], "3500")
        sensitivity = commercial_sensitivity(
            provider=self.provider, supplier_price=100000, policy=self.general, quoted_shipping=10000,
            margins=(20,), customer_charges=(0,),
        )
        self.assertFalse(sensitivity[0]["eligible_by_caps"])

    def test_exception_wins_over_specific_and_general(self):
        specific = PricingPolicy.objects.create(
            name="Specific", precedence="SPECIFIC", priority=10, channel="SHOPIFY", provider=self.provider,
            category="Muebles", target_margin_percent=32,
        )
        exception = PricingPolicy.objects.create(
            name="Exception", precedence="EXCEPTION", priority=1, channel="SHOPIFY", provider=self.provider,
            sku="SKU-1", target_margin_percent=35,
        )
        selected = select_policy(
            [self.general, specific, exception],
            {"channel": "SHOPIFY", "provider_id": self.provider.id, "category": "Muebles", "sku": "SKU-1"},
        )
        self.assertEqual(selected, exception)

    def test_all_shipping_modes_fail_closed_without_verified_logistics_inputs(self):
        options = shipping_options(
            provider=self.provider, supplier_price=100000, policy=self.general,
            quoted_shipping=None, current_product_price=180000, logistics_inputs_complete=False,
        )
        self.assertEqual(len(options), 4)
        self.assertTrue(all(not option["supported"] for option in options))
        self.assertTrue(all(option["status"] == "BLOCKED_MISSING_LOGISTICS_INPUT" for option in options))

    def test_shipping_subsidy_is_blocked_when_current_price_breaks_minimum_margin(self):
        self.general.minimum_margin_percent = Decimal("25")
        self.general.save()
        options = shipping_options(
            provider=self.provider, supplier_price=100000, policy=self.general,
            quoted_shipping=16000, current_product_price=135000, logistics_inputs_complete=True,
        )
        self.assertTrue(any(not option["supported"] for option in options))
        self.assertFalse(next(item for item in options if item["customer_charge"] == 0)["supported"])


class LocalCatalogAPITests(APITestCase):
    @override_settings(DEBUG=True)
    def test_workspace_cache_is_private_and_invalidated_after_catalog_change(self):
        cache.clear()
        first = self.client.get("/api/catalogo/workspace/?page_size=25")
        second = self.client.get("/api/catalogo/workspace/?page_size=25")
        self.assertEqual(first.headers["X-Pamo-Cache"], "MISS")
        self.assertEqual(second.headers["X-Pamo-Cache"], "HIT")
        self.assertIn("private", second.headers["Cache-Control"])

        MasterProduct.objects.create(title="Invalida caché")
        third = self.client.get("/api/catalogo/workspace/?page_size=25")
        self.assertEqual(third.headers["X-Pamo-Cache"], "MISS")

    @override_settings(DEBUG=True)
    def test_workspace_exposes_baru_items_missing_from_shopify(self):
        provider = ProviderConfig.objects.create(name="Barú")
        item = SupplierCatalogItem.objects.create(
            provider=provider,
            source_batch="QA",
            supplier_sku="BARU-FALTA-1",
            description="Producto que aún no existe en Shopify",
            supplier_price=Decimal("119000"),
        )
        SkuReconciliation.objects.create(
            supplier_item=item,
            status=SkuReconciliation.Status.MISSING,
            reason="No existe SKU exacto en Shopify",
        )
        response = self.client.get("/api/catalogo/workspace/?fresh=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["missing_shopify"][0]["supplier_sku"], "BARU-FALTA-1")
        self.assertEqual(response.data["missing_shopify"][0]["supplier_price"], "119000.00")

    @override_settings(DEBUG=True)
    def test_import_plan_is_local_and_not_executable(self):
        response = self.client.get("/api/catalogo/shopify/import-plan/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["execution_allowed"])
        self.assertFalse(response.data["external_writes_enabled"])

    @override_settings(DEBUG=False)
    def test_non_local_anonymous_access_is_denied(self):
        response = self.client.get("/api/catalogo/workspace/")
        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=True)
    def test_policy_from_another_provider_is_rejected(self):
        first = ProviderConfig.objects.create(name="Proveedor 1", tax_treatment="INCLUDED", tax_rate=19)
        second = ProviderConfig.objects.create(name="Proveedor 2", tax_treatment="INCLUDED", tax_rate=19)
        policy = PricingPolicy.objects.create(name="Solo proveedor 1", provider=first, target_margin_percent=30)
        response = self.client.post("/api/catalogo/pricing/simulate/", {
            "provider_id": second.id, "policy_id": policy.id, "supplier_price": "119000",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PRICING_POLICY_PROVIDER_MISMATCH")

    @override_settings(DEBUG=True)
    def test_workspace_and_bulk_pilot_gets_do_not_write_to_sqlite(self):
        before = (IntegrationReadStatus.objects.count(), BulkSimulationRun.objects.count())
        workspace = self.client.get("/api/catalogo/workspace/")
        pilot = self.client.get("/api/catalogo/pilot/simulation/")
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(pilot.status_code, 200)
        self.assertEqual(pilot.data["external_writes"], 0)
        self.assertEqual((IntegrationReadStatus.objects.count(), BulkSimulationRun.objects.count()), before)

    @override_settings(DEBUG=True)
    def test_hypothesis_edit_stays_inactive_and_local(self):
        provider = ProviderConfig.objects.create(name="Barú", tax_treatment="INCLUDED", tax_rate=19)
        policy = PricingPolicy.objects.create(
            name="Hipótesis", provider=provider, active=False, approval_status="HYPOTHESIS",
            target_margin_percent=30, minimum_margin_percent=20, logistics_reserve=12000,
        )
        response = self.client.post("/api/catalogo/pricing/hypothesis/", {
            "policy_id": policy.id, "target_margin_percent": "32", "minimum_margin_percent": "21",
            "channel_commission_percent": "3", "logistics_reserve": "15000",
            "max_shipping_subsidy": "11000", "rounding_increment": 500,
        }, format="json")
        self.assertEqual(response.status_code, 200)
        policy.refresh_from_db()
        self.assertFalse(policy.active)
        self.assertTrue(policy.simulation_only)
        self.assertEqual(policy.reserve_behavior, "CAP")
        self.assertEqual(response.data["external_writes"], 0)


class BaruCatalogParsingTests(TestCase):
    def test_mixed_thousands_separators_are_not_decimals(self):
        self.assertEqual(normalize_thousands_price("$136.842"), Decimal("136842"))
        self.assertEqual(normalize_thousands_price("$184,211"), Decimal("184211"))
        self.assertIsNone(normalize_thousands_price("$184,21"))

    def test_net_cost_is_derived_without_changing_gross(self):
        gross = Decimal("136842")
        self.assertEqual(derive_net_cost(gross, Decimal("19")), Decimal("114993.28"))
        self.assertEqual(gross, Decimal("136842"))


@override_settings(DEBUG=True)
class CatalogPaginationAndFilterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        for index in range(101):
            product = MasterProduct.objects.create(
                shopify_product_id=f"gid://shopify/Product/{index}",
                title=f"Producto {index:03d}", vendor="Barú" if index == 100 else "Otro",
                brand="Marca especial" if index == 100 else "Marca común",
                category="Categoría especial" if index == 100 else "General",
                collections=["Colección especial"] if index == 100 else [],
                quality_score=55, needs_review=True,
            )
            ProductVariant.objects.create(
                product=product,
                shopify_variant_id=f"gid://shopify/ProductVariant/{index}",
                sku=f"SKU-{index:03d}",
                price=100000 + index,
            )
        supplier_only = MasterProduct.objects.create(
            title="Muestra proveedor fuera de Shopify",
            vendor="Barú",
        )
        ProductVariant.objects.create(
            product=supplier_only,
            sku="BARU-SOLO-MUESTRA",
            price=99000,
        )

    def test_catalog_is_paginated_before_serialization(self):
        response = self.client.get("/api/catalogo/workspace/?page=2&page_size=100")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["total"], 101)
        self.assertEqual(response.data["pagination"]["pages"], 2)
        self.assertEqual(len(response.data["products"]), 1)
        self.assertEqual(response.data["pagination"]["unit"], "SHOPIFY_VARIANT")
        self.assertEqual(response.data["summary"]["master_catalog"]["variants"], 101)
        self.assertEqual(
            response.data["summary"]["master_catalog"]["excluded_supplier_only_products"],
            1,
        )

    def test_channel_filters_compare_against_full_shopify_variant_base(self):
        first, second = ProductVariant.objects.filter(
            shopify_variant_id__gt="",
        ).order_by("sku")[:2]
        ChannelSnapshot.objects.create(
            product=first.product,
            variant=first,
            channel="MERCADO_LIBRE",
            state="active",
        )
        ChannelSnapshot.objects.create(
            product=second.product,
            variant=second,
            channel="MERCADO_LIBRE",
            state="paused",
        )

        missing = self.client.get(
            "/api/catalogo/workspace/?channel=MERCADO_LIBRE&channelCoverage=missing&fresh=1",
        )
        active = self.client.get(
            "/api/catalogo/workspace/?channel=MERCADO_LIBRE&channelState=active&fresh=1",
        )

        self.assertEqual(missing.data["pagination"]["total"], 99)
        self.assertEqual(active.data["pagination"]["total"], 1)

    def test_search_and_provider_filter_run_across_the_full_local_database(self):
        response = self.client.get("/api/catalogo/workspace/?search=SKU-100&provider=Bar%C3%BA")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["total"], 1)
        self.assertEqual(response.data["products"][0]["variants"][0]["sku"], "SKU-100")

    def test_facets_and_collection_filter_cover_the_full_shopify_catalog(self):
        response = self.client.get(
            "/api/catalogo/workspace/?collection=Colecci%C3%B3n%20especial&fresh=1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["total"], 1)
        self.assertEqual(response.data["products"][0]["variants"][0]["sku"], "SKU-100")
        self.assertIn("Barú", response.data["facets"]["providers"])
        self.assertIn("Marca especial", response.data["facets"]["brands"])
        self.assertIn("Categoría especial", response.data["facets"]["categories"])
        self.assertIn("Colección especial", response.data["facets"]["collections"])

    def test_excel_column_filter_and_options_cover_all_pages(self):
        response = self.client.get(
            "/api/catalogo/workspace/",
            {"column_filters": json.dumps({"provider": ["Barú"]}), "fresh": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["total"], 1)
        self.assertEqual(response.data["products"][0]["variants"][0]["sku"], "SKU-100")

        options = self.client.get("/api/catalogo/workspace/column-options/?column=provider")
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.data["scope"], "ALL_SHOPIFY_VARIANTS")
        self.assertEqual(options.data["catalog_variants"], 101)
        self.assertEqual(options.data["options"], ["Barú", "Otro"])
        self.assertEqual(options.data["external_writes"], 0)

    def test_siigo_report_runs_from_shopify_master_toward_siigo(self):
        variant = ProductVariant.objects.filter(
            shopify_variant_id__gt="",
        ).order_by("sku").first()
        SiigoProductSnapshot.objects.create(
            siigo_id="SIIGO-LINKED-001",
            sku=variant.sku,
            name=variant.product.title,
            active=True,
            matched_variant=variant,
            match_status=SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY,
            observed_at=timezone.now(),
        )
        SiigoProductSnapshot.objects.create(
            siigo_id="SIIGO-ONLY-001",
            sku="SIIGO-SIN-SHOPIFY",
            name="Producto que solo existe en Siigo",
            active=True,
            match_status=SiigoProductSnapshot.MatchStatus.MISSING_SHOPIFY,
            observed_at=timezone.now(),
        )
        cache.clear()

        workspace = self.client.get("/api/catalogo/workspace/?fresh=1")
        created = self.client.get(
            "/api/catalogo/workspace/?siigoStatus=created&fresh=1",
        )
        missing = self.client.get(
            "/api/catalogo/workspace/?siigoStatus=missing&fresh=1",
        )
        options = self.client.get(
            "/api/catalogo/workspace/column-options/?column=siigo",
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.data["summary"]["siigo_created_shopify"], 1)
        self.assertEqual(workspace.data["summary"]["siigo_missing_shopify"], 100)
        self.assertEqual(
            workspace.data["summary"]["master_catalog"]["channels"]["SIIGO"]["created"],
            1,
        )
        self.assertEqual(
            workspace.data["summary"]["master_catalog"]["channels"]["SIIGO"]["missing"],
            100,
        )
        self.assertEqual(created.data["pagination"]["total"], 1)
        self.assertEqual(missing.data["pagination"]["total"], 100)
        self.assertEqual(options.data["options"], ["CREADO", "FALTA CREAR"])
        self.assertEqual(options.data["catalog_variants"], 101)

    def test_channel_metrics_filter_price_and_shopify_cost_cover_full_catalog(self):
        variant = ProductVariant.objects.filter(shopify_variant_id__gt="").order_by("sku").first()
        ChannelSnapshot.objects.create(
            product=variant.product,
            variant=variant,
            channel="MERCADO_LIBRE",
            state="active",
            price=Decimal("150000"),
            inventory_available=Decimal("3"),
        )
        CostObservation.objects.create(
            variant=variant,
            source="SHOPIFY",
            raw_cost=Decimal("50000"),
            currency="COP",
            tax_treatment="EXCLUDED",
            observed_at=timezone.now(),
        )
        cache.clear()

        price = self.client.get(
            "/api/catalogo/workspace/?channel=MERCADO_LIBRE&priceMin=149000&priceMax=151000&fresh=1",
        )
        with_cost = self.client.get(
            "/api/catalogo/workspace/?costStatus=ready&fresh=1",
        )
        filtered = self.client.get(
            "/api/catalogo/workspace/",
            {
                "column_filters": json.dumps({"MERCADO_LIBRE__status": ["active"]}),
                "fresh": "1",
            },
        )
        price_options = self.client.get(
            "/api/catalogo/workspace/column-options/?column=MERCADO_LIBRE__price",
        )
        profit_options = self.client.get(
            "/api/catalogo/workspace/column-options/?column=MERCADO_LIBRE__profit",
        )
        profit_filtered = self.client.get(
            "/api/catalogo/workspace/",
            {
                "column_filters": json.dumps(
                    {"MERCADO_LIBRE__profit": ["$ 36.148"]},
                ),
                "fresh": "1",
            },
        )

        self.assertEqual(price.data["pagination"]["total"], 1)
        self.assertEqual(with_cost.data["pagination"]["total"], 1)
        self.assertEqual(filtered.data["pagination"]["total"], 1)
        self.assertIn("$ 150.000", price_options.data["options"])
        self.assertEqual(price_options.data["scope"], "ALL_SHOPIFY_VARIANTS")
        self.assertIn("$ 36.148", profit_options.data["options"])
        self.assertEqual(profit_filtered.data["pagination"]["total"], 1)


class InventoryEngineTests(TestCase):
    def setUp(self):
        product = MasterProduct.objects.create(title="Inventario QA")
        self.variant = ProductVariant.objects.create(product=product, sku="INV-QA")

    def test_unknown_inventory_blocks_instead_of_becoming_zero(self):
        snapshot = InventorySourceSnapshot.objects.create(
            variant=self.variant, source_name="Barú", stock_unknown=True,
            observed_at="2026-08-24T12:00:00Z", update_method="FILE", canonical=True,
        )
        result = calculate_available_to_promise([snapshot])
        self.assertTrue(result["blocked"])
        self.assertIsNone(result["quantity"])

    def test_shared_stock_is_allocated_not_duplicated(self):
        allocations = allocate_channels(Decimal("10"), [
            {"channel": "SHOPIFY", "cap": Decimal("6"), "priority": 1},
            {"channel": "MERCADO_LIBRE", "cap": Decimal("6"), "priority": 2},
        ])
        self.assertEqual(sum(item["quantity"] for item in allocations), Decimal("10"))


class ShopifySnapshotImporterTests(TestCase):
    def payload(self):
        return {"data": {"productVariants": {"nodes": [{
            "id": "gid://shopify/ProductVariant/1", "sku": "SHOP-1", "title": "Única",
            "price": "199000", "compareAtPrice": "219000", "inventoryQuantity": 10,
            "updatedAt": "2026-08-24T20:00:00Z", "metafields": {"nodes": [{"namespace": "custom", "key": "material", "type": "single_line_text_field", "jsonValue": "Plata"}]},
            "inventoryItem": {"unitCost": {"amount": "119000", "currencyCode": "COP"}, "tracked": True, "requiresShipping": True,
                "measurement": {"weight": {"unit": "KILOGRAMS", "value": 1.2}},
                "inventoryLevels": {"nodes": [{"id": "level-1", "location": {"id": "loc-1", "name": "Bodega 1"}, "quantities": [{"name": "available", "quantity": 7}], "updatedAt": "2026-08-24T20:00:00Z"}]}},
            "product": {"id": "gid://shopify/Product/1", "title": "Producto Shopify", "vendor": "Barú", "productType": "Accesorio", "status": "ACTIVE", "tags": ["plata"],
                "collections": {"nodes": [{"id": "col-1", "title": "Plata"}]},
                "metafields": {"nodes": [{"namespace": "custom", "key": "garantia", "type": "single_line_text_field", "jsonValue": "1 año"}]},
                "media": {"nodes": []}}}], "pageInfo": {"hasNextPage": False, "endCursor": "cursor-1"}}}}

    def run_import(self):
        with patch("sys.stdin", StringIO(json.dumps(self.payload()))):
            call_command("import_shopify_snapshot", stdout=StringIO())

    def test_rich_snapshot_is_idempotent_and_preserves_evidence(self):
        self.run_import()
        self.run_import()
        variant = ProductVariant.objects.get(sku="SHOP-1")
        self.assertEqual(ProductVariant.objects.count(), 1)
        self.assertEqual(variant.compare_at_price, Decimal("219000"))
        self.assertEqual(CostObservation.objects.filter(variant=variant, source="SHOPIFY").count(), 1)
        self.assertEqual(InventoryLevel.objects.filter(variant=variant).count(), 1)
        self.assertEqual(ProductMetafield.objects.filter(product=variant.product).count(), 1)
        snapshot = ChannelSnapshot.objects.get(variant=variant, channel="SHOPIFY")
        self.assertEqual(snapshot.cost, Decimal("119000"))
        self.assertEqual(snapshot.inventory_available, Decimal("10"))
        self.assertTrue(snapshot.payload["inventoryLocationsPartial"])


class EnviaSnapshotImporterTests(TestCase):
    def test_sanitized_quote_import_is_idempotent_and_never_external(self):
        product = MasterProduct.objects.create(title="Envío QA")
        ProductVariant.objects.create(product=product, sku="ENV-QA")
        payload = {"records": [{
            "sku": "ENV-QA", "basis": "CHECKOUT_ESTIMATE", "destination": {"city": "Bogotá"},
            "weight_kg": "2.5", "dimensions": {"length_cm": 30, "width_cm": 20, "height_cm": 10},
            "carrier": "QA", "amount": "16500", "currency": "COP",
            "external_reference_hash": "a" * 64, "order_reference_hash": "b" * 64,
            "observed_at": "2026-08-24T20:00:00Z", "evidence_reference": "QA sanitized fixture",
        }]}
        for _ in range(2):
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                call_command("import_envia_snapshot", stdout=StringIO())
        quote = LogisticsQuoteSnapshot.objects.get()
        self.assertEqual(quote.amount, Decimal("16500"))
        self.assertEqual(quote.external_writes, 0)
        self.assertEqual(quote.external_reference_hash, "a" * 64)


class ProviderDataImporterTests(TestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(name="Barú", tax_treatment="INCLUDED", tax_rate=19)
        self.item = SupplierCatalogItem.objects.create(
            provider=self.provider, source_batch="qa", supplier_sku="BARU-QA", supplier_price=119000,
            missing_fields=["inventory", "weight", "dimensions"],
        )
        product = MasterProduct.objects.create(title="Barú QA")
        variant = ProductVariant.objects.create(product=product, sku="BARU-QA", shopify_variant_id="1")
        SkuReconciliation.objects.create(supplier_item=self.item, variant=variant, status="EXACT", reason="QA")

    def payload(self):
        return (
            "sku,weight_kg,length_cm,width_cm,height_cm,warehouse_external_id,warehouse_name,reported_stock,reserved_stock,safety_stock,observed_at,freshness_minutes,update_method,evidence_reference\n"
            "BARU-QA,1.25,30,20,10,B1,Bodega proveedor,8,1,2,2026-08-25T00:00:00Z,1440,FILE,Archivo oficial proveedor\n"
        )

    def test_physical_inventory_import_is_idempotent_and_never_infers(self):
        for _ in range(2):
            with patch("sys.stdin", StringIO(self.payload())):
                call_command("import_provider_data", provider="Barú", source_filename="baru_fisicos.csv", stdout=StringIO())
        self.item.refresh_from_db()
        self.assertEqual(self.item.weight_kg, Decimal("1.250"))
        self.assertEqual(self.item.dimensions["length_cm"], "30")
        self.assertEqual(self.item.inventory, Decimal("8"))
        self.assertEqual(ProviderDataImport.objects.count(), 1)
        self.assertEqual(SupplierItemInventorySnapshot.objects.count(), 1)
        source = InventorySourceSnapshot.objects.get(source_name="Proveedor Barú")
        self.assertEqual(source.available_to_promise, Decimal("5"))
        self.assertEqual(source.evidence_reference, "Archivo oficial proveedor")


class SiigoProbeImporterTests(TestCase):
    def test_probe_preserves_unknown_cost_and_is_idempotent_by_warehouse(self):
        product = MasterProduct.objects.create(title="Siigo QA")
        variant = ProductVariant.objects.create(product=product, sku="SIIGO-QA")
        SiigoProductSnapshot.objects.create(
            siigo_id="siigo-1", sku="SIIGO-QA", name="Siigo QA", matched_variant=variant,
            match_status="EXACT_SHOPIFY", observed_at="2026-08-25T00:00:00Z",
        )
        payload = {
            "source": {"observed_at": "2026-08-25T01:00:00Z"},
            "probes": [{
                "sku": "SIIGO-QA", "cost_candidates": [], "available_quantity": "4",
                "warehouses": [{"id": "W1", "name": "Principal", "quantity": "4"}],
            }],
            "summary": {"products_probed": 1, "with_cost_candidate": 0, "warehouse_count": 1},
        }
        for _ in range(2):
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                call_command("import_siigo_probe_snapshot", stdout=StringIO())
        snapshot = SiigoProductSnapshot.objects.get(siigo_id="siigo-1")
        self.assertEqual(snapshot.cost_status, "NOT_PROVIDED_BY_PRODUCT_DETAIL")
        self.assertEqual(CostObservation.objects.filter(source="SIIGO").count(), 0)
        self.assertEqual(InventorySourceSnapshot.objects.filter(source_name="Siigo bodega").count(), 1)
        self.assertEqual(IntegrationReadStatus.objects.get(system="SIIGO", capability="verified_cost").external_writes, 0)


class PhysicalEnrichmentTests(TestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(name="Barú físico QA", tax_treatment="INCLUDED", tax_rate=19)
        self.item = SupplierCatalogItem.objects.create(
            provider=self.provider, source_batch="qa-physical", supplier_sku="PHY-QA", supplier_price=119000,
            missing_fields=["dimensions"],
        )
        self.product = MasterProduct.objects.create(
            title="Producto físico QA", description_html="<p>Dimensiones del producto: 30 x 20 x 10 cm. Peso del paquete: 2 kg.</p>",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="PHY-QA", barcode="7701234567890", shopify_variant_id="gid://variant/phy-qa",
        )
        SkuReconciliation.objects.create(supplier_item=self.item, variant=self.variant, status="EXACT", reason="QA")
        ChannelSnapshot.objects.create(
            product=self.product, variant=self.variant, channel="SHOPIFY", state="ACTIVE", external_variant_id="gid://variant/phy-qa",
            observed_at=timezone.now(), payload={
                "requiresShipping": True, "weight": {"value": 2, "unit": "KILOGRAMS"},
                "variantMetafields": [{
                    "namespace": "logistica", "key": "largo_paquete", "type": "dimension",
                    "jsonValue": {"value": 35, "unit": "CENTIMETERS"}, "updatedAt": "2026-08-25T01:00:00Z",
                }],
            },
        )

    def test_units_are_positive_bounded_and_idempotent(self):
        self.assertEqual(normalize_measurement("LENGTH", "100", "mm"), (Decimal("10.0000"), "CM"))
        self.assertEqual(normalize_measurement("WEIGHT", "2.20462262", "POUNDS"), (Decimal("1.0000"), "KG"))
        with self.assertRaises(PhysicalValidationError):
            normalize_measurement("HEIGHT", 0, "CM")
        with self.assertRaises(PhysicalValidationError):
            normalize_measurement("LENGTH", 600, "CM")
        with self.assertRaises(PhysicalValidationError):
            normalize_measurement("VOLUME", 10, "CM")

    def test_description_separates_product_dimensions_from_package_weight(self):
        findings = description_measurements(self.product.description_html)
        dimensions = [row for row in findings if row["field"] != "WEIGHT"]
        weight = next(row for row in findings if row["field"] == "WEIGHT")
        self.assertEqual({row["scope"] for row in dimensions}, {"PRODUCT"})
        self.assertEqual(weight["scope"], "PACKAGE")

    def test_local_extraction_is_idempotent_and_preview_stays_blocked(self):
        analyze_local_item(self.item, self.variant)
        first_count = PhysicalEvidenceCandidate.objects.count()
        package_length = PhysicalEvidenceCandidate.objects.get(source_reference="Shopify variant metafield")
        self.assertEqual(package_length.scope, "PACKAGE")
        self.assertEqual(package_length.classification, "CONFIRMED")
        analyze_local_item(self.item, self.variant)
        self.assertEqual(PhysicalEvidenceCandidate.objects.count(), first_count)
        preview = build_shopify_preview(self.variant)
        self.assertEqual(preview.status, "BLOCKED")
        self.assertIn("MISSING_APPROVED_PACKAGE_LENGTH", preview.blockers)
        self.assertEqual(preview.external_writes, 0)
        first_selection = select_pilot(SupplierCatalogItem.objects.filter(pk=self.item.pk), limit=25)[0]
        second_selection = select_pilot(SupplierCatalogItem.objects.filter(pk=self.item.pk), limit=25)[0]
        self.assertEqual(first_selection.id, second_selection.id)

    def test_zero_weight_from_real_snapshot_is_skipped_without_aborting_batch(self):
        snapshot = ChannelSnapshot.objects.get(variant=self.variant, channel="SHOPIFY")
        snapshot.payload = {**snapshot.payload, "weight": {"value": 0, "unit": "KILOGRAMS"}}
        snapshot.save(update_fields=["payload"])
        analyze_local_item(self.item, self.variant)
        self.assertFalse(PhysicalEvidenceCandidate.objects.filter(
            source_reference="Shopify inventoryItem.measurement.weight",
        ).exists())

    def test_comma_decimal_from_description_is_persisted_as_decimal(self):
        self.product.description_html = "<p>Dimensiones del producto: 33,5 x 20 x 10 cm.</p>"
        self.product.save(update_fields=["description_html"])
        analyze_local_item(self.item, self.variant)
        candidate = PhysicalEvidenceCandidate.objects.get(
            source_reference="Shopify product description_html", field="LENGTH",
        )
        self.assertEqual(candidate.original_value, Decimal("33.5"))

    def test_public_import_exact_is_idempotent_and_similar_is_forced_to_product_estimate(self):
        payload = {"records": [
            {
                "sku": "PHY-QA", "field": "LENGTH", "scope": "PRODUCT", "value": 30, "unit": "CM",
                "source_type": "PUBLIC_RETAIL_EXACT", "source_url": "https://example.test/exact",
                "source_reference": "Comercio exacto", "identifier_type": "SKU", "identifier_value": "PHY-QA",
                "evidence_excerpt": "SKU PHY-QA, largo 30 cm", "confidence": "0.8",
            },
            {
                "sku": "PHY-QA", "field": "WIDTH", "scope": "PACKAGE", "value": 25, "unit": "CM",
                "source_type": "PUBLIC_SIMILAR", "source_url": "https://example.test/similar",
                "source_reference": "Producto similar", "identifier_type": "MODEL", "identifier_value": "OTRO",
                "evidence_excerpt": "Modelo parecido, ancho 25 cm", "confidence": "0.3",
            },
        ]}
        for _ in range(2):
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                call_command("import_public_physical_evidence", stdout=StringIO())
        self.assertEqual(PhysicalEvidenceCandidate.objects.filter(source_type="PUBLIC_RETAIL_EXACT").count(), 1)
        similar = PhysicalEvidenceCandidate.objects.get(source_type="PUBLIC_SIMILAR")
        self.assertEqual(similar.scope, "PRODUCT")
        self.assertEqual(similar.classification, "ESTIMATED")
        self.assertEqual(similar.external_writes, 0)

    def test_conflicting_package_values_are_both_flagged(self):
        common = dict(
            variant=self.variant, supplier_item=self.item, field="LENGTH", scope="PACKAGE", classification="CONFIRMED",
            source_type="MANUAL", evidence_excerpt="Medición de empaque", original_unit="CM", confidence=Decimal("0.99"),
        )
        first = upsert_candidate(source_reference="Proveedor A", original_value=10, **common)
        second = upsert_candidate(source_reference="Proveedor B", original_value=20, **common)
        first.refresh_from_db()
        self.assertTrue(first.conflict)
        self.assertTrue(second.conflict)

    def test_package_smaller_than_product_is_flagged(self):
        product = upsert_candidate(
            variant=self.variant, supplier_item=self.item, field="WIDTH", scope="PRODUCT", classification="DERIVED",
            source_type="SHOPIFY_DESCRIPTION", source_reference="Ficha propia", evidence_excerpt="Ancho producto 40 cm",
            original_value=40, original_unit="CM", confidence=Decimal("0.8"),
        )
        package = upsert_candidate(
            variant=self.variant, supplier_item=self.item, field="WIDTH", scope="PACKAGE", classification="CONFIRMED",
            source_type="MANUAL", source_reference="Medición de empaque", evidence_excerpt="Ancho paquete 30 cm",
            original_value=30, original_unit="CM", confidence=Decimal("0.99"),
        )
        product.refresh_from_db()
        self.assertTrue(product.conflict)
        self.assertTrue(package.conflict)

    @override_settings(DEBUG=True)
    def test_estimate_and_conflict_cannot_be_approved(self):
        candidate = upsert_candidate(
            variant=self.variant, supplier_item=self.item, field="LENGTH", scope="PRODUCT", classification="ESTIMATED",
            source_type="PUBLIC_SIMILAR", source_reference="Producto similar", source_url="https://example.test/similar",
            evidence_excerpt="Producto similar 30 cm", original_value=30, original_unit="CM", confidence=Decimal("0.3"),
        )
        response = self.client.post("/api/catalogo/physical/review-queue/", {"candidate_id": candidate.id, "action": "APPROVE_LOCAL"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "ESTIMATE_CANNOT_BE_APPROVED_FOR_SHIPPING")
        self.assertEqual(PhysicalEvidenceDecision.objects.count(), 0)

    @override_settings(DEBUG=True)
    def test_review_get_is_read_only(self):
        analyze_local_item(self.item, self.variant)
        before = (PhysicalEvidenceCandidate.objects.count(), PhysicalEvidenceDecision.objects.count(), ShopifyPhysicalUpdatePreview.objects.count())
        response = self.client.get("/api/catalogo/physical/review-queue/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["external_writes"], 0)
        self.assertEqual(response.data["summary"]["shopify_zero_weight_exact"], 0)
        self.assertEqual((PhysicalEvidenceCandidate.objects.count(), PhysicalEvidenceDecision.objects.count(), ShopifyPhysicalUpdatePreview.objects.count()), before)


@override_settings(DEBUG=True)
class PhysicalMeasurementImportTests(APITestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(name="Barú", tax_treatment="INCLUDED", tax_rate=19)
        self.item = SupplierCatalogItem.objects.create(
            provider=self.provider, source_batch="phase5-qa", supplier_sku="PKG-QA", supplier_price=119000,
            description="Producto empaque QA", missing_fields=["weight", "dimensions"],
        )
        self.product = MasterProduct.objects.create(title="Producto empaque QA")
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="PKG-QA", barcode="7700000000001", shopify_variant_id="gid://variant/pkg-qa",
        )
        SkuReconciliation.objects.create(supplier_item=self.item, variant=self.variant, status="EXACT", reason="QA")
        PhysicalEnrichmentPilotSelection.objects.create(variant=self.variant, supplier_item=self.item, score=100, criteria=["QA"], rank=1)

    def csv_payload(self, *, source="MEDICION_FISICA", weight="2.5", packages="1", length="30"):
        return (
            "SKU,GTIN,Descripción,Proveedor,Peso empacado,Unidad peso,Largo paquete,Ancho paquete,Alto paquete,Unidad dimensiones,Cantidad bultos,Fecha verificación,Responsable,Tipo de fuente,Fuente / referencia,Evidencia / foto (URL),Observaciones\n"
            f"SKU: PKG-QA,GTIN: 7700000000001,QA,Barú,{weight},KG,{length},20,10,CM,{packages},2026-08-25,Operador QA,{source},Báscula y cinta QA,https://example.test/evidence.jpg,Medición controlada\n"
        ).encode()

    def test_real_import_is_idempotent_requires_review_and_reverses_locally(self):
        first = preview_measurement_import("Barú", "medicion.csv", self.csv_payload())
        second = preview_measurement_import("Barú", "medicion.csv", self.csv_payload())
        self.assertEqual(first.id, second.id)
        self.assertEqual(PhysicalMeasurementImportBatch.objects.count(), 1)
        self.assertEqual(PhysicalMeasurementImportRow.objects.count(), 1)
        apply_measurement_import(first.id, actor_label="qa")
        apply_measurement_import(first.id, actor_label="qa")
        candidates = PhysicalEvidenceCandidate.objects.filter(variant=self.variant, scope="PACKAGE")
        self.assertEqual(candidates.count(), 4)
        self.assertTrue(all(candidate.classification == "CONFIRMED" for candidate in candidates))
        preview = ShopifyPhysicalUpdatePreview.objects.get(variant=self.variant)
        self.assertEqual(preview.status, "BLOCKED")
        for candidate in candidates:
            PhysicalEvidenceDecision.objects.create(
                candidate=candidate, action="APPROVE_LOCAL", reason="QA local", actor_label="qa", external_writes=0,
            )
        preview = build_shopify_preview(self.variant)
        self.assertEqual(preview.status, "READY_LOCAL")
        evidence = {
            field.lower(): {"candidate_id": candidate.id, "scope": "PACKAGE", "classification": "CONFIRMED", "decision": "APPROVE_LOCAL"}
            for field, candidate in ((candidate.field, candidate) for candidate in candidates)
        }
        run = run_fixture_quote({
            "destination": {"city": "Bogotá", "state": "Bogotá D.C.", "country": "CO"},
            "package": {"length": 30, "width": 20, "height": 10, "weight": 2.5, "scope": "PACKAGE", "evidence_classification": "CONFIRMED", "evidence": evidence},
        }, {"quotes": [{"carrier": "QA", "amount": "16000", "currency": "COP"}]})
        self.assertEqual(run.external_writes, 0)
        reverse_measurement_import(first.id, actor_label="qa")
        self.assertEqual(build_shopify_preview(self.variant).status, "BLOCKED")
        self.assertEqual(CatalogHistoryEvent.objects.filter(entity_type="PHYSICAL_MEASUREMENT_IMPORT").count(), 2)

    def test_demo_never_creates_evidence_or_quote_eligibility(self):
        batch = preview_measurement_import("Barú", "demo.csv", self.csv_payload(source="DEMO_NO_CONFIRMADO"))
        self.assertTrue(batch.is_demo)
        self.assertEqual(batch.status, "DEMO_VALIDATED")
        apply_measurement_import(batch.id, actor_label="qa")
        self.assertEqual(PhysicalEvidenceCandidate.objects.count(), 0)
        self.assertEqual(batch.rows.get().status, "DEMO_BLOCKED")
        with self.assertRaises(EnviaQuoteContractError):
            validate_quote_request({
                "destination": {"city": "Bogotá", "state": "Bogotá D.C.", "country": "CO"},
                "package": {"length": 30, "width": 20, "height": 10, "weight": 2.5, "scope": "PACKAGE", "evidence_classification": "DEMO_NO_CONFIRMADO"},
            })

    def test_zero_absurd_and_multipack_rows_fail_closed(self):
        zero = preview_measurement_import("Barú", "zero.csv", self.csv_payload(weight="0"))
        self.assertEqual(zero.error_rows, 1)
        self.assertTrue(any(error.startswith("INVALID_WEIGHT") for error in zero.rows.get().errors))
        absurd = preview_measurement_import("Barú", "absurd.csv", self.csv_payload(length="700"))
        self.assertEqual(absurd.error_rows, 1)
        multipack = preview_measurement_import("Barú", "multi.csv", self.csv_payload(packages="2"))
        self.assertEqual(multipack.conflict_rows, 1)
        with self.assertRaises(PhysicalMeasurementImportError):
            apply_measurement_import(multipack.id)

    def test_workspace_get_is_read_only_and_tasks_are_local(self):
        before = (PhysicalMeasurementImportBatch.objects.count(), PhysicalMeasurementTask.objects.count())
        response = self.client.get("/api/catalogo/physical/measurement-workspace/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["external_writes"], 0)
        self.assertEqual((PhysicalMeasurementImportBatch.objects.count(), PhysicalMeasurementTask.objects.count()), before)
        task = self.client.post("/api/catalogo/physical/measurement-workspace/", {
            "action": "CREATE_TASK", "variant_id": str(self.variant.id), "task_action": "REQUEST_PROVIDER", "actor_label": "qa",
        }, format="json")
        self.assertEqual(task.status_code, 200)
        self.assertEqual(PhysicalMeasurementTask.objects.get().external_writes, 0)

    def test_template_is_downloadable_and_xlsx_is_previewed_with_missing_fields(self):
        response = self.client.get("/api/catalogo/physical/measurement-template/")
        self.assertEqual(response.status_code, 200)
        path = Path(__file__).resolve().parent / "assets" / "plantilla_baru_medidas_paquete_25_sku.xlsx"
        batch = preview_measurement_import("Barú", path.name, path.read_bytes())
        self.assertEqual(batch.total_rows, 25)
        self.assertEqual(batch.error_rows, 25)
        self.assertEqual(batch.external_writes, 0)


class EnviaQuoteContractTests(TestCase):
    def payload(self):
        evidence = {
            field: {"candidate_id": f"fixture-{field}", "scope": "PACKAGE", "classification": "CONFIRMED", "decision": "APPROVE_LOCAL"}
            for field in ("length", "width", "height", "weight")
        }
        return {
            "destination": {"city": "Bogotá", "state": "Bogotá D.C.", "country": "CO"},
            "package": {"length": 30, "width": 20, "height": 10, "weight": 2, "scope": "PACKAGE", "evidence_classification": "CONFIRMED", "evidence": evidence},
        }

    def test_product_or_estimated_measurement_is_rejected(self):
        product = self.payload()
        product["package"]["scope"] = "PRODUCT"
        with self.assertRaises(EnviaQuoteContractError):
            validate_quote_request(product)
        estimated = self.payload()
        estimated["package"]["evidence_classification"] = "ESTIMATED"
        with self.assertRaises(EnviaQuoteContractError):
            validate_quote_request(estimated)

    def test_error_or_zero_never_becomes_free_shipping(self):
        with self.assertRaises(EnviaQuoteContractError):
            validate_quote_response({"quotes": [{"carrier": "QA", "amount": 0}]})

    def test_fixture_is_idempotent_non_binding_and_creates_no_guide(self):
        fixture = {"quotes": [{"carrier": "QA", "service": "Terrestre", "amount": "16500", "currency": "COP", "estimated_days": 2}]}
        first = run_fixture_quote(self.payload(), fixture)
        second = run_fixture_quote(self.payload(), fixture)
        self.assertEqual(first.id, second.id)
        self.assertEqual(EnviaQuoteContractRun.objects.count(), 1)
        self.assertFalse(second.response_snapshot["binding"])
        self.assertFalse(second.response_snapshot["guide_created"])
        self.assertEqual(second.external_writes, 0)
