from django.contrib.auth import get_user_model
from django.test import TestCase

from .shipping_delivery import ShippingDeliveryInputError, simulate_standard_shipping


class ShippingDeliveryPhase1Tests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="shipping-phase1@test.invalid"))

    def test_workspace_exposes_only_standard_shipping_and_no_external_writes(self):
        response = self.client.get("/api/catalogo/shipping-delivery/workspace/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["code"] for item in payload["customer_options"]], ["STANDARD"])
        self.assertEqual(payload["commercial_policy"]["minimum_margin_percent"], "20.00")
        self.assertFalse(payload["destination"]["postal_code_required"])
        self.assertFalse(payload["phase_2"]["active"])
        self.assertEqual(payload["external_writes"], 0)
        self.assertFalse(payload["execution_allowed_external"])

    def test_wholesale_discount_is_checked_after_shipping_subsidy(self):
        result = simulate_standard_shipping({
            "city": "Bogotá",
            "department": "Bogotá D.C.",
            "fulfillment_origin": "ENVIA",
            "order_subtotal": "200000",
            "product_cost_total": "100000",
            "wholesale_discount_percent": "10",
            "standard_shipping_estimate": "18000",
            "customer_shipping_charge": "8000",
        })
        self.assertEqual(result["option"]["code"], "STANDARD")
        self.assertEqual(result["commercial"]["company_shipping_subsidy"], "10000")
        self.assertEqual(result["commercial"]["margin_percent"], "38.89")
        self.assertTrue(result["commercial"]["margin_protected"])
        self.assertEqual(result["external_writes"], 0)

    def test_below_twenty_percent_requires_review_and_never_becomes_free(self):
        response = self.client.post(
            "/api/catalogo/shipping-delivery/workspace/",
            {
                "city": "Cali",
                "department": "Valle del Cauca",
                "fulfillment_origin": "SUPPLIER",
                "order_subtotal": "150000",
                "product_cost_total": "120000",
                "wholesale_discount_percent": "10",
                "standard_shipping_estimate": "20000",
                "customer_shipping_charge": "5000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["decision"]["code"], "MANUAL_APPROVAL_REQUIRED")
        self.assertFalse(payload["commercial"]["margin_protected"])
        self.assertFalse(payload["option"]["free_shipping"])
        self.assertIn("proveedor", " ".join(payload["warnings"]).lower())

    def test_city_department_and_standard_estimate_are_required(self):
        with self.assertRaises(ShippingDeliveryInputError):
            simulate_standard_shipping({
                "city": "",
                "department": "Antioquia",
                "fulfillment_origin": "ENVIA",
                "order_subtotal": "100000",
                "product_cost_total": "50000",
                "standard_shipping_estimate": "15000",
            })
        response = self.client.post("/api/catalogo/shipping-delivery/workspace/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["external_writes"], 0)

    def test_invalid_origin_is_rejected_without_writes(self):
        response = self.client.post(
            "/api/catalogo/shipping-delivery/workspace/",
            {
                "city": "Medellín",
                "department": "Antioquia",
                "fulfillment_origin": "UNILOGIX",
                "order_subtotal": "100000",
                "product_cost_total": "50000",
                "standard_shipping_estimate": "15000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["external_writes"], 0)
