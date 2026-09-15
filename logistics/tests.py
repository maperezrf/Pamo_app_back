import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
        response = self.client.post("/api/logistics/remittances/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Remittance.objects.get(pk=response.data["id"])

    def test_operator_can_create_send_and_confirm_without_exposing_cost(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post("/api/logistics/remittances/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("supplier_unit_cost", response.data["lines"][0])
        self.assertEqual(response.data["requester_name"], "PERSONA PRUEBA")
        self.assertEqual(response.data["current_state"], "DRAFT")

        remittance_id = response.data["id"]
        sent = self.client.post(f"/api/logistics/remittances/{remittance_id}/send-to-confirm/")
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(sent.data["current_state"], "SENT_TO_CONFIRM")

        confirmation = self.client.post(
            f"/api/logistics/remittances/{remittance_id}/confirm/",
            {"expected_version": sent.data["version"]},
            format="json",
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.data)
        self.assertEqual(confirmation.data["number"], "RD-0001")
        self.assertEqual(confirmation.data["current_state"], "PENDING_INVOICE")

    def test_outsider_cannot_list(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get("/api/logistics/remittances/").status_code, 403)

    def test_confirmation_requires_sent_to_confirm_state(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            f"/api/logistics/remittances/{remittance.id}/confirm/",
            {"expected_version": remittance.version},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)

    def test_confirmation_returns_conflict_for_stale_version(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.operator)
        self.client.post(f"/api/logistics/remittances/{remittance.id}/send-to-confirm/")
        response = self.client.post(
            f"/api/logistics/remittances/{remittance.id}/confirm/",
            {"expected_version": remittance.version + 5},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)

    def test_confirmation_returns_not_found_for_unknown_uuid(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            f"/api/logistics/remittances/{uuid.uuid4()}/confirm/",
            {"expected_version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 404, response.data)

    def test_cancel_requires_reason_and_records_state(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.operator)
        missing_reason = self.client.post(f"/api/logistics/remittances/{remittance.id}/cancel/", {})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.data)

        response = self.client.post(
            f"/api/logistics/remittances/{remittance.id}/cancel/", {"reason": "Pedido duplicado"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["current_state"], "CANCELLED")

    def test_cannot_cancel_twice(self):
        remittance = self.create_draft()
        self.client.force_authenticate(self.operator)
        self.client.post(f"/api/logistics/remittances/{remittance.id}/cancel/", {"reason": "Motivo"})
        response = self.client.post(f"/api/logistics/remittances/{remittance.id}/cancel/", {"reason": "Motivo 2"})
        self.assertEqual(response.status_code, 409, response.data)
