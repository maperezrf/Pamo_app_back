from decimal import Decimal

from django.test import SimpleTestCase

from facturacion.functions.remittance_domain import (
    RemittanceDomainError,
    calculate_margin_price,
    calculate_siigo_invoice_price,
    calculate_supplier_commercials,
    ensure_expected_version,
    invoice_readiness,
    normalize_draft,
)
from facturacion.functions.supplier_invoice import normalize_supplier_invoice_payload, parse_supplier_invoice_text


IDS = {
    "warehouse": "11111111-1111-4111-8111-111111111111",
    "supplier": "22222222-2222-4222-8222-222222222222",
    "customer": "33333333-3333-4333-8333-333333333333",
}


def draft_payload(**overrides):
    payload = {
        "warehouse_id": IDS["warehouse"],
        "supplier_party_id": IDS["supplier"],
        "customer_party_id": IDS["customer"],
        "requester_name": "Camila",
        "delivery_method": "COURIER",
        "lines": [{"quantity": 2, "description": "Válvula industrial", "usage_destination": "Restaurante calle 85"}],
    }
    payload.update(overrides)
    return payload


class RemittanceRecoveryDomainTests(SimpleTestCase):
    def test_supplier_invoice_derives_unit_cost_from_total_and_quantity(self):
        parsed = normalize_supplier_invoice_payload({
            "lines": [{
                "sku": "abc-1",
                "quantity": 4,
                "description": "grifería",
                "unitPrice": None,
                "totalPrice": 10000,
                "discountPercent": None,
                "discountValue": None,
                "confidence": 0.9,
                "warning": None,
            }],
            "globalDiscountPercent": None,
            "globalDiscountValue": None,
            "otherCharges": None,
            "freightCost": None,
            "warnings": [],
        })
        self.assertEqual(parsed["lines"][0]["supplier_unit_cost"], 2500.0)
        self.assertEqual(parsed["lines"][0]["description"], "GRIFERÍA")

    def test_digital_supplier_invoice_pdf_text_is_parsed_without_ai(self):
        parsed = parse_supplier_invoice_text("""
Nombre producto                               Cant.                   Vr. Total
mez lvm poste bajo negro serrat                  2.00                  233,999.99

Descuentos                                                              21,848.74
Subtotal                                                               196,638.65
IVA 19%                                                                 37,361.34
Total                                                                  233,999.99
""")

        self.assertEqual(len(parsed["lines"]), 1)
        self.assertEqual(parsed["lines"][0]["description"], "MEZ LVM POSTE BAJO NEGRO SERRAT")
        self.assertEqual(parsed["lines"][0]["quantity"], 2.0)
        self.assertEqual(parsed["lines"][0]["supplier_unit_cost"], 117000.0)
        self.assertEqual(parsed["lines"][0]["supplier_line_total"], 233999.99)
        self.assertEqual(parsed["global_discount_value"], 21848.74)
        self.assertTrue(any("IVA 19%" in warning for warning in parsed["warnings"]))

    def test_ambiguous_pdf_text_is_not_guessed(self):
        self.assertIsNone(parse_supplier_invoice_text("TOTAL 100000\nCLIENTE DE PRUEBA"))

    def test_normalizes_draft_without_invoice_data(self):
        draft = normalize_draft(draft_payload())
        self.assertEqual(draft["requester_name"], "CAMILA")
        self.assertEqual(draft["lines"][0]["description"], "VÁLVULA INDUSTRIAL")
        self.assertEqual(draft["lines"][0]["usage_destination"], "RESTAURANTE CALLE 85")
        self.assertNotIn("number", draft)
        self.assertNotIn("siigo_sku", draft["lines"][0])
        self.assertNotIn("invoice_unit_price", draft["lines"][0])

    def test_keeps_private_supplier_reference_separate_from_sale_price(self):
        draft = normalize_draft(draft_payload(lines=[{
            "quantity": 3,
            "description": "Grifería ducha",
            "supplier_sku": " prov-123 ",
            "supplier_unit_cost": "12500.50",
            "supplier_line_total": "37501.50",
        }]))
        line = draft["lines"][0]
        self.assertEqual(line["supplier_sku"], "PROV-123")
        self.assertEqual(line["supplier_unit_cost"], Decimal("12500.50"))
        self.assertNotIn("invoice_unit_price", line)

    def test_derives_unit_cost_when_invoice_only_has_line_total(self):
        line = normalize_draft(draft_payload(lines=[{
            "quantity": 3,
            "description": "Grifería ducha",
            "supplier_line_total": 100000,
        }]))["lines"][0]
        self.assertEqual(line["supplier_unit_cost"], Decimal("33333.33"))
        self.assertEqual(line["supplier_line_total"], Decimal("100000.00"))

    def test_preserves_explicit_unit_cost_when_total_is_also_present(self):
        line = normalize_draft(draft_payload(lines=[{
            "quantity": 3,
            "description": "Grifería ducha",
            "supplier_unit_cost": 32000,
            "supplier_line_total": 100000,
        }]))["lines"][0]
        self.assertEqual(line["supplier_unit_cost"], Decimal("32000.00"))

    def test_applies_supplier_discount_charges_tax_margin_and_rounding(self):
        line = calculate_supplier_commercials(
            [{"quantity": 2, "supplier_unit_cost": 11900, "supplier_discount_percent": 10}],
            {
                "margin_rate": "0.35",
                "price_includes_tax": True,
                "source_price_basis": "UNIT",
                "tax_rate": 19,
                "rounding_increment": 100,
            },
            {
                "supplier_global_discount_percent": 5,
                "supplier_other_charges": 1000,
                "supplier_freight_cost": 2000,
            },
        )[0]
        self.assertEqual(line["net_unit_cost"], Decimal("9810.50"))
        self.assertEqual(line["suggested_invoice_unit_price"], Decimal("15100.00"))

    def test_line_total_can_be_authoritative_source(self):
        line = calculate_supplier_commercials(
            [{"quantity": 4, "supplier_unit_cost": 1, "supplier_line_total": 40000}],
            {"margin_rate": "0.20", "source_price_basis": "LINE_TOTAL", "rounding_increment": 100},
        )[0]
        self.assertEqual(line["net_unit_cost"], Decimal("10000.00"))
        self.assertEqual(line["suggested_invoice_unit_price"], Decimal("12500.00"))

    def test_margin_price_uses_cost_divided_by_remaining_margin_and_rounds_up(self):
        self.assertEqual(
            calculate_margin_price(Decimal("10000.00"), Decimal("35.000"), "100"),
            Decimal("15400.00"),
        )
        self.assertEqual(
            calculate_margin_price(Decimal("10000.00"), Decimal("40.000"), "100"),
            Decimal("16700.00"),
        )

    def test_margin_price_rejects_one_hundred_percent(self):
        with self.assertRaises(RemittanceDomainError) as context:
            calculate_margin_price(Decimal("10000.00"), Decimal("100.000"), "100")
        self.assertEqual(context.exception.code, "INVALID_MARGIN")

    def test_siigo_price_is_always_before_tax(self):
        self.assertEqual(
            calculate_siigo_invoice_price(
                Decimal("106075.63"), Decimal("35.000"),
                tax_rate="19", tax_included=True, rounding_increment="100",
            ),
            Decimal("163200.00"),
        )
        self.assertEqual(
            calculate_siigo_invoice_price(
                Decimal("106075.63"), Decimal("35.000"),
                tax_rate="19", tax_included=False, rounding_increment="100",
            ),
            Decimal("163200.00"),
        )

    def test_rejects_unknown_delivery_method(self):
        with self.assertRaises(RemittanceDomainError):
            normalize_draft(draft_payload(delivery_method="DRONE"))

    def test_personal_pickup_is_not_converted_to_carrier(self):
        draft = normalize_draft(draft_payload(delivery_method="PERSONAL_PICKUP"))
        self.assertEqual(draft["delivery_method"], "PERSONAL_PICKUP")
        self.assertNotEqual(draft["delivery_method"], "CARRIER")

    def test_personal_pickup_can_be_invoice_ready_while_signature_is_pending(self):
        base = {
            "document_status": "CONFIRMED",
            "delivery_status": "PENDING",
            "delivery": {"method": "PERSONAL_PICKUP"},
            "lines": [{
                "master_product_id": "product-id",
                "invoice_description": "Producto",
                "invoice_unit_price": Decimal("100"),
            }],
        }
        self.assertTrue(invoice_readiness(base))
        self.assertFalse(invoice_readiness({**base, "delivery": {"method": "CARRIER"}}))
        self.assertFalse(invoice_readiness({**base, "lines": [{**base["lines"][0], "master_product_id": None}]}))

    def test_expected_version_blocks_stale_write(self):
        ensure_expected_version(3, 3)
        with self.assertRaises(RemittanceDomainError) as context:
            ensure_expected_version(4, 3)
        self.assertEqual(context.exception.code, "VERSION_CONFLICT")
