from django.test import SimpleTestCase

from .commercial_costs import enrich_commercial_payload


class CommercialCostRulesTests(SimpleTestCase):
    def profitability(self, channel, price="100000", payload=None, cost=None):
        return enrich_commercial_payload(
            channel, price, payload, cost=cost
        ).get("profitability")

    def test_shopify_uses_drive_cost_structure(self):
        result = self.profitability("SHOPIFY")

        self.assertEqual(result["commission_amount"], "4867")
        self.assertEqual(result["other_cost_amount"], "30560")
        self.assertEqual(result["commission_basis"], "MERCADO_PAGO_CO_PUBLIC_IMMEDIATE_RATE")
        self.assertEqual(result["commission_formula_label"], "3.29% + $800 + IVA")
        self.assertNotIn("Pasarela", " · ".join(result["other_cost_labels"]))
        self.assertFalse(result["verified"])

    def test_shopify_simulation_guarantees_target_and_caps_logistics_reserve(self):
        regular = self.profitability("SHOPIFY", cost="100000")["pricing_simulation"]
        capped = self.profitability("SHOPIFY", price="900000", cost="600000")["pricing_simulation"]

        self.assertEqual(regular["status"], "SIMULATED_LOCAL")
        self.assertEqual(regular["achieved_net_margin_percent"], "20.0")
        self.assertEqual(regular["logistics_reserve_basis"], "PERCENT")
        self.assertEqual(capped["logistics_reserve_amount"], "40000")
        self.assertEqual(capped["logistics_reserve_basis"], "CAPPED")
        self.assertEqual(capped["external_writes"], 0)

    def test_mercado_libre_api_fee_overrides_historical_percentage(self):
        result = self.profitability(
            "MERCADO_LIBRE",
            payload={
                "selling_fees": {
                    "sale_fee_amount": "13000",
                    "percentage_fee": "13",
                }
            },
        )

        self.assertEqual(result["commission_amount"], "13000")
        self.assertEqual(result["commission_percent"], "13")
        self.assertEqual(result["other_cost_amount"], "31352")
        self.assertEqual(result["source"]["commission"], "Mercado Libre API")
        self.assertNotIn("Pasarela", " · ".join(result["other_cost_labels"]))
        self.assertIn("Alistamiento y bodegaje $7.200", result["other_cost_labels"])

    def test_falabella_uses_its_reviewed_drive_parameters(self):
        result = self.profitability("FALABELLA")

        self.assertEqual(result["commission_amount"], "34000")
        self.assertEqual(result["other_cost_amount"], "27560")

    def test_sodimac_has_no_commission_and_keeps_transport_separate(self):
        result = self.profitability("SODIMAC")

        self.assertEqual(result["commission_amount"], "0")
        self.assertEqual(result["other_cost_amount"], "27000")

    def test_unreviewed_channel_is_not_invented(self):
        payload = {"source": "existing"}

        self.assertEqual(enrich_commercial_payload("MADECENTRO", "100000", payload), payload)
