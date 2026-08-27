from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import LogisticsQuoteSnapshot
from .phase7 import (
    average_shipping_for_variant,
    build_average_shipping_reference,
    build_historical_profiles,
    protected_margin_preview,
    shipping_tariff_band,
)
from .models import MasterProduct, ProductVariant


class Phase7LocalLogisticsTests(TestCase):
    def setUp(self):
        for index, amount in enumerate((10000, 12000, 20000, 30000)):
            LogisticsQuoteSnapshot.objects.create(
                provider="ENVIA", basis="REALIZED_GUIDE", status="AVAILABLE",
                destination={"postal_code_prefix": "110"}, weight_kg="0.5",
                dimensions={"length_cm": 30, "width_cm": 20, "height_cm": 20},
                carrier="ground", amount=amount, currency="COP",
                evidence_reference="fixture historical local", observed_at=timezone.now(),
                fingerprint=f"profile-{index}", external_writes=0,
            )
        self.client.force_login(get_user_model().objects.create_user(username="phase7@test.invalid"))

    def test_profiles_use_segment_median_and_p75_and_fail_closed_on_unmapped_origin(self):
        profiles, count, rejected = build_historical_profiles()
        self.assertEqual((count, rejected), (4, 0))
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["median_cop"], Decimal("16000"))
        self.assertEqual(profiles[0]["conservative_p75_cop"], Decimal("22500"))
        self.assertFalse(profiles[0]["assignable"])
        self.assertIn("HISTORICAL_GUIDE_WITHOUT_PROVIDER_ORIGIN", profiles[0]["blockers"])

    def test_estimated_shipping_protects_margin_and_unknown_never_becomes_zero(self):
        protected = protected_margin_preview(200000, 100000, 20000, 20)
        broken = protected_margin_preview(120000, 100000, 20000, 20)
        unknown = protected_margin_preview(120000, 100000, None, 20)
        self.assertEqual(protected["status"], "PROTECTED")
        self.assertIn("ESTIMATED_SHIPPING_BREAKS_MARGIN", broken["blockers"])
        self.assertIn("PRICE_COST_OR_ESTIMATE_UNKNOWN", unknown["blockers"])

    def test_workspace_exposes_six_channels_without_connections_or_writes(self):
        response = self.client.get("/api/catalogo/phase7/workspace/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["channels"]), 6)
        self.assertEqual(payload["external_writes"], 0)
        self.assertFalse(payload["execution_allowed_external"])
        self.assertEqual(payload["siigo_architecture_decision"]["status"], "FUTURE_SEPARATE_MODULE")

    def test_average_shipping_is_frequency_weighted_trimmed_and_informational(self):
        for index in range(20):
            LogisticsQuoteSnapshot.objects.create(
                provider="ENVIA", basis="REALIZED_GUIDE", status="AVAILABLE",
                destination={"city": "Bogotá"}, weight_kg="1",
                dimensions={"length_cm": 20, "width_cm": 15, "height_cm": 15},
                carrier="estandar", amount=10000 if index < 19 else 100000,
                currency="COP", evidence_reference="fixture average",
                observed_at=timezone.now(), fingerprint=f"average-{index}", external_writes=0,
            )
        reference = build_average_shipping_reference()
        self.assertEqual(reference["classification"], "ESTIMATED_INFORMATIONAL_ONLY")
        self.assertEqual(reference["amount"], Decimal("11500"))
        self.assertEqual(reference["sample_size"], 24)
        self.assertEqual(reference["volumetric_divisor_reference"], 5000)

    def test_unknown_package_uses_global_history_without_faking_measurements(self):
        variant = ProductVariant.objects.create(
            product=MasterProduct.objects.create(title="Sin medidas"), sku="NO-DIMS",
        )
        result = average_shipping_for_variant(variant, build_average_shipping_reference())
        self.assertEqual(result["tariff_band"], "SIN_DATOS")
        self.assertEqual(result["package_basis"], "GLOBAL_HISTORY_FALLBACK")
        self.assertTrue(result["uses_global_fallback"])
        self.assertEqual(shipping_tariff_band(None, {}), "SIN_DATOS")
