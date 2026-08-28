import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from .functions.siigo_invoice import (
    SiigoPreflightError,
    SiigoWriteError,
    build_live_preflight,
    customer_policy,
    stable_idempotency_key,
)
from .functions.issue_siigo_invoice import SiigoIssuanceError, issue_controlled_siigo_draft
from .models import (
    Remittance,
    RemittanceInvoiceAttempt,
    RemittanceLine,
    RemittanceParty,
    RemittanceWarehouse,
)


class Lines:
    def __init__(self, *items):
        self.items = items

    def all(self):
        return self.items


class FakeSiigoClient:
    def __init__(self, *, duplicate_documents=False, missing_customer=False):
        self.duplicate_documents = duplicate_documents
        self.missing_customer = missing_customer
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if path == "/v1/customers":
            return {"results": [] if self.missing_customer else [{
                "id": "customer-lao-kao",
                "identification": "830047537",
                "branch_office": 0,
                "active": True,
            }]}
        if path == "/v1/customers/customer-lao-kao":
            return {
                "id": "customer-lao-kao",
                "identification": "830047537",
                "name": ["LAO KAO S.A."],
                "vat_responsible": True,
                "fiscal_responsibilities": [{"code": "R-99-PN", "name": "No aplica"}],
                "related_users": {"seller_id": "seller-1"},
            }
        if path == "/v1/document-types":
            results = [{
                "id": 101,
                "name": "Factura electrónica",
                "active": True,
                "prefix": "FV",
                "electronic_type": "Facturación electrónica",
            }]
            if self.duplicate_documents:
                results.append({**results[0], "id": 102, "prefix": "FE"})
            return {"results": results}
        if path == "/v1/payment-types":
            return {"results": [{"id": 201, "name": "Crédito", "active": True, "due_date": True}]}
        if path == "/v1/users":
            return {"results": [{"id": "seller-1", "active": True, "first_name": "Camila"}]}
        if path == "/v1/taxes":
            return {"results": [
                {"id": "13456", "type": "ReteFuente", "percentage": 2.5, "active": True},
                {"id": "13457", "type": "ReteICA", "percentage": 11.04, "active": True},
            ]}
        if path == "/v1/products":
            return {"results": [{
                "id": "product-serv-01",
                "code": "SERV_01",
                "name": "Producto genérico",
                "active": True,
                "tax_included": True,
                "taxes": [{"id": "iva-19", "type": "IVA", "percentage": 19}],
            }]}
        raise AssertionError(f"Consulta inesperada: {path} {params}")


def remittance_fixture():
    return SimpleNamespace(
        id=uuid.UUID("a7a44e76-3c15-4a06-bf20-fbb0f594edd0"),
        number="RD-0006",
        version=4,
        customer=SimpleNamespace(nit="830047537", name="LAO KAO S.A."),
        lines=Lines(SimpleNamespace(
            line_number=1,
            siigo_sku="SERV_01",
            invoice_description="MEZ LVM POSTE BAJO NEGRO SERRAT",
            original_description="MEZ LVM POSTE BAJO NEGRO SERRAT",
            quantity=Decimal("2"),
            invoice_unit_price=Decimal("176800"),
        )),
    )


class SiigoInvoicePreflightTests(SimpleTestCase):
    def test_lao_kao_policy_uses_reteica_per_thousand_equivalent(self):
        policy = customer_policy("830.047.537")
        self.assertEqual(policy["payment_days"], 15)
        self.assertEqual(policy["retentions"][0]["calculation_percentage"], Decimal("2.5"))
        self.assertEqual(policy["retentions"][1]["siigo_percentage"], Decimal("11.04"))
        self.assertEqual(policy["retentions"][1]["calculation_percentage"], Decimal("1.104"))
        self.assertIn("11,04‰", policy["retentions"][1]["label"])

    def test_idempotency_key_is_stable_alphanumeric_and_short(self):
        remittance = remittance_fixture()
        first = stable_idempotency_key(remittance)
        self.assertEqual(first, stable_idempotency_key(remittance))
        self.assertLessEqual(len(first), 30)
        self.assertTrue(first.isalnum())

    def test_builds_exact_controlled_draft_without_stamp_or_mail(self):
        result = build_live_preflight(
            remittance_fixture(),
            client=FakeSiigoClient(),
            today=date(2026, 8, 26),
        )
        self.assertEqual(result["status"], "READY_FOR_CONTROLLED_DRAFT")
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["customer"]["id"], "customer-lao-kao")
        self.assertEqual(result["payment"]["due_date"], "2026-09-10")
        self.assertEqual([item["id"] for item in result["payload"]["retentions"]], ["13457"])
        self.assertEqual(
            result["payload"]["items"][0]["taxes"],
            [{"id": "iva-19"}, {"id": "13456"}],
        )
        self.assertEqual(result["retentions"][0]["placement"], "ITEM_TAX")
        self.assertEqual(result["retentions"][1]["placement"], "INVOICE_RETENTION")
        self.assertEqual(result["payload"]["stamp"], {"send": False})
        self.assertEqual(result["payload"]["mail"], {"send": False})
        self.assertEqual(result["summary"]["gross_total"], Decimal("420784.00"))
        self.assertEqual(result["summary"]["tax_total"], Decimal("67184.00"))
        self.assertEqual(result["summary"]["retentions_total"], Decimal("12743.74"))
        self.assertEqual(result["summary"]["payment_total"], Decimal("408040.26"))

    def test_missing_customer_blocks_instead_of_guessing(self):
        with self.assertRaises(SiigoPreflightError) as raised:
            build_live_preflight(
                remittance_fixture(),
                client=FakeSiigoClient(missing_customer=True),
                today=date(2026, 8, 26),
            )
        self.assertEqual(raised.exception.code, "SIIGO_CONFIGURATION_MISSING")

    def test_ambiguous_document_blocks_until_exact_id_is_configured(self):
        with self.assertRaises(SiigoPreflightError) as raised:
            build_live_preflight(
                remittance_fixture(),
                client=FakeSiigoClient(duplicate_documents=True),
                today=date(2026, 8, 26),
            )
        self.assertEqual(raised.exception.code, "SIIGO_CONFIGURATION_AMBIGUOUS")

        result = build_live_preflight(
            remittance_fixture(),
            client=FakeSiigoClient(duplicate_documents=True),
            document_id="102",
            today=date(2026, 8, 26),
        )
        self.assertEqual(result["document"]["id"], "102")


class FakeWriteClient:
    def __init__(self, *, unknown=False):
        self.calls = 0
        self.unknown = unknown

    def create_invoice(self, payload, *, idempotency_key):
        self.calls += 1
        if self.unknown:
            raise SiigoWriteError(
                "Resultado desconocido",
                code="SIIGO_WRITE_RESULT_UNKNOWN",
                outcome_unknown=True,
            )
        return {"id": "invoice-draft-1", "name": "FV-1-999", "stamp": {"status": "Draft"}}


class SiigoControlledDraftTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="billing@example.com")
        warehouse = RemittanceWarehouse.objects.create(name="Bodega QA")
        supplier = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.SUPPLIER,
            nit="900000001",
            name="Proveedor QA",
        )
        customer, _ = RemittanceParty.objects.get_or_create(
            party_type=RemittanceParty.PartyType.CUSTOMER,
            nit="830047537",
            defaults={"name": "LAO KAO S.A."},
        )
        self.remittance = Remittance.objects.create(
            number="RD-0999",
            warehouse=warehouse,
            supplier=supplier,
            customer=customer,
            requester_name="ARLEY",
            document_status=Remittance.DocumentStatus.CONFIRMED,
            invoice_status=Remittance.InvoiceStatus.READY,
            created_by=self.actor,
        )
        RemittanceLine.objects.create(
            remittance=self.remittance,
            line_number=1,
            quantity=Decimal("2.000"),
            original_description="PRODUCTO QA",
            siigo_sku="SERV_01",
            invoice_unit_price=Decimal("176800"),
        )
        self.key = stable_idempotency_key(self.remittance)
        self.attempt = RemittanceInvoiceAttempt.objects.create(
            remittance=self.remittance,
            idempotency_key=self.key,
            status=RemittanceInvoiceAttempt.Status.PREVIEWED,
            sanitized_result={
                "status": "READY_FOR_CONTROLLED_DRAFT",
                "payload": {
                    "document": {"id": 26647},
                    "stamp": {"send": False},
                    "mail": {"send": False},
                },
            },
            created_by=self.actor,
        )

    def test_success_is_persisted_and_second_call_reuses_same_result(self):
        client = FakeWriteClient()
        first = issue_controlled_siigo_draft(self.remittance, actor=self.actor, client=client)
        second = issue_controlled_siigo_draft(self.remittance, actor=self.actor, client=client)
        self.attempt.refresh_from_db()
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.attempt.status, RemittanceInvoiceAttempt.Status.SUCCEEDED)
        self.assertEqual(self.attempt.external_invoice_id, "invoice-draft-1")
        self.assertFalse(first["dian_sent"])
        self.assertTrue(second["reused"])
        self.assertEqual(self.remittance.audit_events.filter(event_type="SIIGO_DRAFT_CREATED").count(), 1)

    def test_unknown_result_blocks_blind_retry(self):
        client = FakeWriteClient(unknown=True)
        with self.assertRaises(SiigoIssuanceError):
            issue_controlled_siigo_draft(self.remittance, actor=self.actor, client=client)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT)
        with self.assertRaises(SiigoIssuanceError) as raised:
            issue_controlled_siigo_draft(self.remittance, actor=self.actor, client=client)
        self.assertEqual(raised.exception.code, "SIIGO_RECONCILIATION_REQUIRED")
        self.assertEqual(client.calls, 1)
