from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .envia_readiness import serialize_variant_envia_readiness, serialize_variant_shipping_intelligence
from .models import (
    ChannelSnapshot,
    ExternalChannelProductSnapshot,
    LogisticsQuoteSnapshot,
    MasterProduct,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    ProductVariant,
)


class EnviaReadinessTests(TestCase):
    def setUp(self):
        product = MasterProduct.objects.create(title="Producto QA", status="ACTIVE")
        self.variant = ProductVariant.objects.create(product=product, sku="ENVIA-QA-1")

    def add_field(self, field, action=PhysicalEvidenceDecision.Action.APPROVE_LOCAL):
        candidate = PhysicalEvidenceCandidate.objects.create(
            variant=self.variant,
            field=field,
            scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
            classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
            source_type=PhysicalEvidenceCandidate.SourceType.MANUAL,
            source_reference=f"medición física {field}",
            evidence_excerpt=f"{field}=10",
            observed_at=timezone.now(),
            extraction_method="MANUAL_QA",
            original_value=Decimal("10"),
            original_unit="KG" if field == "WEIGHT" else "CM",
            normalized_value=Decimal("10"),
            normalized_unit="KG" if field == "WEIGHT" else "CM",
            confidence=Decimal("1"),
            content_fingerprint=f"envia-readiness-{field}",
        )
        PhysicalEvidenceDecision.objects.create(
            candidate=candidate,
            action=action,
            reason="QA local",
        )
        return candidate

    def test_missing_fields_fail_closed(self):
        self.add_field(PhysicalEvidenceCandidate.Field.WEIGHT)
        result = serialize_variant_envia_readiness(self.variant)
        self.assertEqual(result["status"], "MISSING_PACKAGE_DATA")
        self.assertEqual(result["missing_fields"], ["HEIGHT", "LENGTH", "WIDTH"])
        self.assertIsNone(result["current_quote_amount"])

    def test_complete_package_becomes_ready_and_quote_is_separate(self):
        for field in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT"):
            self.add_field(field)
        ready = serialize_variant_envia_readiness(self.variant)
        self.assertEqual(ready["status"], "READY_TO_QUOTE")

        LogisticsQuoteSnapshot.objects.create(
            variant=self.variant,
            provider="ENVIA",
            basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
            status="AVAILABLE",
            destination={"city": "Bogotá"},
            amount=Decimal("16500"),
            currency="COP",
            carrier="QA Transportadora",
            observed_at=timezone.now(),
            fingerprint="envia-readiness-current-quote",
        )
        quoted = serialize_variant_envia_readiness(self.variant)
        self.assertEqual(quoted["status"], "CURRENT_QUOTE_AVAILABLE")
        self.assertEqual(quoted["current_quote_amount"], Decimal("16500"))

    def test_latest_rejection_revokes_previous_approval(self):
        candidate = self.add_field(PhysicalEvidenceCandidate.Field.WEIGHT)
        PhysicalEvidenceDecision.objects.create(
            candidate=candidate,
            action=PhysicalEvidenceDecision.Action.REJECT,
            reason="medición invalidada",
        )
        result = serialize_variant_envia_readiness(self.variant)
        self.assertFalse(result["weight_confirmed"])
        self.assertIn("WEIGHT", result["missing_fields"])

    def test_multiple_rate_options_require_selection_instead_of_picking_cheapest(self):
        for field in ("WEIGHT", "LENGTH", "WIDTH", "HEIGHT"):
            self.add_field(field)
        observed_at = timezone.now()
        for index, amount in enumerate((Decimal("12000"), Decimal("18000")), start=1):
            LogisticsQuoteSnapshot.objects.create(
                variant=self.variant,
                provider="ENVIA",
                basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
                status="AVAILABLE",
                destination={"city": "11001000"},
                amount=amount,
                currency="COP",
                carrier=f"Carrier {index}",
                observed_at=observed_at,
                fingerprint=f"envia-multi-option-{index}",
            )
        result = serialize_variant_envia_readiness(self.variant)
        self.assertEqual(result["status"], "CURRENT_QUOTE_OPTIONS_AVAILABLE")
        self.assertIsNone(result["current_quote_amount"])
        self.assertEqual(result["current_quote_min_amount"], Decimal("12000"))
        self.assertEqual(result["current_quote_max_amount"], Decimal("18000"))
        self.assertTrue(result["current_quote_selection_required"])

    def test_marketplace_seller_and_buyer_costs_remain_separate(self):
        observed_at = timezone.now()
        external = ExternalChannelProductSnapshot.objects.create(
            channel="MERCADO_LIBRE",
            external_product_id="MCO123",
            external_variant_id="",
            sku=self.variant.sku,
            matched_variant=self.variant,
            match_status=ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU,
            price=Decimal("99000"),
            currency="COP",
            observed_at=observed_at,
            payload={"shipping_costs": {
                "status": "AVAILABLE",
                "seller_estimate": 12500,
                "current_seller_estimate": 8500,
                "seller_estimate_strategy": "MAX_ELIGIBLE_QUOTE_OR_HISTORICAL_P75",
                "seller_currency": "COP",
                "buyer_charge": 0,
                "buyer_list_cost": 17000,
                "buyer_currency": "COP",
                "buyer_destination": {"city": {"name": "Bogotá"}},
                "basis": {"seller": "SELLER_QA", "buyer": "BUYER_QA"},
                "free_shipping": True,
                "current_logistic_type": "cross_docking",
                "modalities": {
                    "collecta": {
                        "eligible": True,
                        "seller_estimate": 8500,
                        "historical_p75": 12500,
                        "historical_samples": 162,
                    },
                    "flex": {
                        "eligible": True,
                        "seller_estimate": 8910,
                        "historical_p75": 8910,
                        "historical_samples": 72,
                    },
                },
            }},
        )
        ChannelSnapshot.objects.create(
            product=self.variant.product,
            variant=self.variant,
            channel="MERCADO_LIBRE",
            external_product_id="MCO123",
            payload={"external_snapshot_id": str(external.id)},
            observed_at=observed_at,
        )
        result = serialize_variant_shipping_intelligence(self.variant, {str(external.id): external})
        meli = result["channels"]["MERCADO_LIBRE"]
        self.assertEqual(meli["seller_estimate"], 12500)
        self.assertEqual(meli["buyer_charge"], 0)
        self.assertEqual(meli["current_seller_estimate"], 8500)
        self.assertEqual(meli["modalities"]["collecta"]["historical_p75"], 12500)
        self.assertEqual(meli["modalities"]["flex"]["seller_estimate"], 8910)
        self.assertEqual(result["reference"]["source"], "MERCADO_LIBRE")
        self.assertEqual(result["reference"]["amount"], 12500)
        self.assertFalse(result["recommended_metric"]["available"])

    def test_realized_history_adds_separate_average_shipping_reference(self):
        LogisticsQuoteSnapshot.objects.create(
            provider="ENVIA", basis=LogisticsQuoteSnapshot.Basis.REALIZED_GUIDE,
            status="AVAILABLE", destination={"city": "Bogotá"}, weight_kg=1,
            dimensions={"length_cm": 20, "width_cm": 15, "height_cm": 15},
            carrier="estandar", amount=Decimal("12500"), currency="COP",
            observed_at=timezone.now(), fingerprint="envia-average-reference",
        )
        result = serialize_variant_shipping_intelligence(self.variant)
        self.assertEqual(result["average_shipping"]["amount"], Decimal("12500"))
        self.assertEqual(result["average_shipping"]["tariff_band"], "HASTA_1_KG_ASUMIDO")
        self.assertTrue(result["average_shipping"]["requires_review"])
        self.assertTrue(result["recommended_metric"]["available"])
        self.assertIsNone(result["reference"])
