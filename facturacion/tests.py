import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Remittance, RemittanceParty, RemittanceWarehouse


User = get_user_model()


class RemittanceAPITests(APITestCase):
    def setUp(self):
        self.operator = User.objects.create_user(username="operator@example.com", email="operator@example.com")
        self.accountant = User.objects.create_user(username="accountant@example.com", email="accountant@example.com")
        self.outsider = User.objects.create_user(username="outsider@example.com", email="outsider@example.com")
        self.operator.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.accountant.groups.add(Group.objects.get_or_create(name="Facturacion")[0])
        self.warehouse = RemittanceWarehouse.objects.create(name="Bodega QA", is_default=True)
        self.supplier = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.SUPPLIER,
            nit="900000001",
            name="Proveedor QA",
        )
        self.customer = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.CUSTOMER,
            nit="830000001",
            name="Cliente QA",
            siigo_id="siigo-customer-qa",
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
        response = self.client.post("/api/facturacion/remisiones/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Remittance.objects.get(pk=response.data["id"])

    def test_operator_can_create_and_confirm_without_exposing_cost(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post("/api/facturacion/remisiones/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("supplier_unit_cost", response.data["lines"][0])
        self.assertEqual(response.data["requester_name"], "PERSONA PRUEBA")

        confirmation = self.client.post(
            f"/api/facturacion/remisiones/{response.data['id']}/confirmar/",
            {"expected_version": response.data["version"]},
            format="json",
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.data)
        self.assertEqual(confirmation.data["number"], "RD-0001")
        self.assertEqual(confirmation.data["document_status"], "CONFIRMED")

    def test_outsider_cannot_list_operational_or_accounting_queue(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get("/api/facturacion/remisiones/").status_code, 403)
        self.assertEqual(self.client.get("/api/facturacion/remisiones/contabilidad/").status_code, 403)

    def test_confirmation_returns_conflict_for_stale_version(self):
        remittance = self.create_draft()
        response = self.client.post(
            f"/api/facturacion/remisiones/{remittance.id}/confirmar/",
            {"expected_version": remittance.version + 1},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)

    def test_confirmation_returns_not_found_for_unknown_uuid(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            f"/api/facturacion/remisiones/{uuid.uuid4()}/confirmar/",
            {"expected_version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 404, response.data)

    def test_accounting_queue_exposes_private_fields_only_to_accounting(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.accountant)
        response = self.client.get("/api/facturacion/remisiones/contabilidad/")
        self.assertEqual(response.status_code, 200, response.data)
        row = next(item for item in response.data if item["id"] == str(remittance.id))
        self.assertEqual(row["lines"][0]["supplier_unit_cost"], "10000.00")

    @override_settings()
    def test_invoice_preview_and_external_write_gate(self):
        remittance = self.create_draft()
        line = remittance.lines.get()
        line.siigo_sku = "SKU-QA"
        line.invoice_description = "PRODUCTO QA"
        line.invoice_unit_price = "20000.00"
        line.save()

        self.client.force_authenticate(self.accountant)
        preview = self.client.get(f"/api/facturacion/remisiones/{remittance.id}/factura/vista-previa/")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["subtotal"], 40000.0)
        self.assertFalse(preview.data["external_writes_enabled"])

        confirmation = self.client.post(f"/api/facturacion/remisiones/{remittance.id}/factura/confirmar/")
        self.assertEqual(confirmation.status_code, 503)
        self.assertEqual(confirmation.data["code"], "EXTERNAL_WRITES_DISABLED")
