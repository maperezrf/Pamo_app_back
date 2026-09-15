from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from integrations.siigo import SiigoAPIError
from logistics.models import Remittance, RemittanceParty, RemittanceWarehouse

from .models import RemittanceInvoiceAttempt, RemittanceInvoiceLine

User = get_user_model()


class AccountingAPITests(APITestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username="operator@example.com", email="operator@example.com")
        self.accountant = User.objects.create_user(username="accountant@example.com", email="accountant@example.com")
        self.outsider = User.objects.create_user(username="outsider@example.com", email="outsider@example.com")
        self.operator.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.accountant.groups.add(Group.objects.get_or_create(name="Facturacion")[0])
        self.warehouse = RemittanceWarehouse.objects.create(name="Bodega QA", is_default=True)
        self.supplier = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.SUPPLIER, nit="900000001", name="Proveedor QA",
        )
        self.customer = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.CUSTOMER, nit="830000001", name="Cliente QA", siigo_id="siigo-customer-qa",
        )

    def payload(self):
        return {
            "warehouse": self.warehouse.pk,
            "supplier": self.supplier.pk,
            "customer": self.customer.pk,
            "requester_name": "Persona prueba",
            "requester_document": "",
            "lines": [{
                "quantity": "2.000",
                "original_description": "grifería de prueba",
                "usage_destination": "calle 80",
                "supplier_sku": "PRIVATE-1",
                "supplier_unit_cost": "10000.00",
            }],
            "delivery": {"method": "PERSONAL_PICKUP", "notes": "Firma posterior"},
        }

    def create_draft(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post("/api/logistics/remittances/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Remittance.objects.get(pk=response.data["id"])

    def confirmed_remittance(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.operator)
        self.client.post(f"/api/logistics/remittances/{remittance.id}/send-to-confirm/")
        remittance.refresh_from_db()
        self.client.post(
            f"/api/logistics/remittances/{remittance.id}/confirm/",
            {"expected_version": remittance.version}, format="json",
        )
        remittance.refresh_from_db()
        return remittance

    def confirmed_and_coded_remittance(self):
        remittance = self.confirmed_remittance()
        line = remittance.lines.get()
        RemittanceInvoiceLine.objects.update_or_create(
            remittance_line=line,
            defaults={"siigo_sku": "SKU-QA", "invoice_description": "PRODUCTO QA", "invoice_unit_price": "20000.00"},
        )
        return remittance

    def invoice_payload(self):
        return {"document_id": 1, "seller_id": 2, "payment_type_id": 3}

    def test_outsider_cannot_list_accounting_queue(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get("/api/accounting/remittances/").status_code, 403)

    def test_accounting_queue_exposes_private_fields_only_to_accounting(self):
        remittance = self.confirmed_remittance()
        self.client.force_authenticate(self.accountant)
        response = self.client.get("/api/accounting/remittances/")
        self.assertEqual(response.status_code, 200, response.data)
        row = next(item for item in response.data if item["id"] == str(remittance.id))
        self.assertEqual(row["lines"][0]["supplier_unit_cost"], "10000.00")

    def test_invoice_preview_and_external_write_gate(self):
        remittance = self.confirmed_and_coded_remittance()
        self.client.force_authenticate(self.accountant)
        preview = self.client.get(f"/api/accounting/remittances/{remittance.id}/invoice/preview/")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["subtotal"], 40000.0)
        self.assertFalse(preview.data["external_writes_enabled"])

        confirmation = self.client.post(f"/api/accounting/remittances/{remittance.id}/invoice/confirm/")
        self.assertEqual(confirmation.status_code, 503)
        self.assertEqual(confirmation.data["code"], "EXTERNAL_WRITES_DISABLED")

    @patch("accounting.views.SIIGO_INVOICE_WRITES_ENABLED", True)
    @patch("accounting.views.EXTERNAL_WRITES_ENABLED", True)
    @patch("accounting.functions.create_siigo_invoice.SiigoClient")
    def test_invoice_confirm_succeeds_and_is_idempotent(self, mock_client_class):
        mock_client_class.return_value.create_invoice.return_value = {
            "id": "siigo-invoice-1", "name": "FV-1-1", "date": "2026-08-24",
        }
        remittance = self.confirmed_and_coded_remittance()
        self.client.force_authenticate(self.accountant)

        first = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["status"], RemittanceInvoiceAttempt.Status.SUCCEEDED)
        self.assertEqual(first.data["external_invoice_id"], "siigo-invoice-1")
        remittance.refresh_from_db()
        self.assertEqual(remittance.current_state, Remittance.State.INVOICED)

        second = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data["external_invoice_id"], "siigo-invoice-1")
        mock_client_class.return_value.create_invoice.assert_called_once()

    @patch("accounting.views.SIIGO_INVOICE_WRITES_ENABLED", True)
    @patch("accounting.views.EXTERNAL_WRITES_ENABLED", True)
    @patch("accounting.functions.create_siigo_invoice.SiigoClient")
    def test_invoice_confirm_marks_failed_on_siigo_http_error(self, mock_client_class):
        mock_client_class.return_value.create_invoice.side_effect = SiigoAPIError("Siigo rechazó la factura (400).")
        remittance = self.confirmed_and_coded_remittance()
        self.client.force_authenticate(self.accountant)

        response = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(response.status_code, 502, response.data)
        remittance.refresh_from_db()
        self.assertEqual(remittance.current_state, Remittance.State.INVOICE_FAILED)
        attempt = RemittanceInvoiceAttempt.objects.get(remittance=remittance)
        self.assertEqual(attempt.status, RemittanceInvoiceAttempt.Status.FAILED)

    @patch("accounting.views.SIIGO_INVOICE_WRITES_ENABLED", True)
    @patch("accounting.views.EXTERNAL_WRITES_ENABLED", True)
    @patch("accounting.functions.create_siigo_invoice.SiigoClient")
    def test_invoice_confirm_marks_unknown_result_and_blocks_retry(self, mock_client_class):
        mock_client_class.return_value.create_invoice.side_effect = requests.exceptions.ConnectionError("boom")
        remittance = self.confirmed_and_coded_remittance()
        self.client.force_authenticate(self.accountant)

        response = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(response.status_code, 502, response.data)
        attempt = RemittanceInvoiceAttempt.objects.get(remittance=remittance)
        self.assertEqual(attempt.status, RemittanceInvoiceAttempt.Status.UNKNOWN_RESULT)

        retry = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(retry.status_code, 400, retry.data)
        mock_client_class.return_value.create_invoice.assert_called_once()

    @patch("accounting.views.SIIGO_INVOICE_WRITES_ENABLED", True)
    @patch("accounting.views.EXTERNAL_WRITES_ENABLED", True)
    def test_invoice_confirm_rejects_uninvoiceable_state(self):
        remittance = self.create_draft()
        line = remittance.lines.get()
        RemittanceInvoiceLine.objects.update_or_create(
            remittance_line=line,
            defaults={"siigo_sku": "SKU-QA", "invoice_description": "PRODUCTO QA", "invoice_unit_price": "20000.00"},
        )
        self.client.force_authenticate(self.accountant)
        response = self.client.post(
            f"/api/accounting/remittances/{remittance.id}/invoice/confirm/", self.invoice_payload(), format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)

    @patch("accounting.views.SIIGO_INVOICE_WRITES_ENABLED", True)
    @patch("accounting.views.EXTERNAL_WRITES_ENABLED", True)
    def test_invoice_confirm_requires_siigo_fields(self):
        remittance = self.confirmed_and_coded_remittance()
        self.client.force_authenticate(self.accountant)
        response = self.client.post(f"/api/accounting/remittances/{remittance.id}/invoice/confirm/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("document_id", response.data)
        self.assertIn("seller_id", response.data)
        self.assertIn("payment_type_id", response.data)
