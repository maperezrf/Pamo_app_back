import base64
import io
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from pypdf import PdfReader
from PIL import Image, ImageDraw

from .functions.recipient_completion import hash_token
from .models import (
    Remittance,
    RemittanceParty,
    RemittanceShareLink,
    RemittanceUsageDestination,
    RemittanceWarehouse,
)


User = get_user_model()


def signature_png():
    output = io.BytesIO()
    image = Image.new("RGB", (180, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(12, 42), (45, 15), (70, 45), (115, 18), (165, 36)], fill="black", width=4)
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


SIGNATURE_PNG = signature_png()


class RecipientCompletionAPITests(APITestCase):
    def setUp(self):
        self.private_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.private_media.name)
        self.media_override.enable()
        self.operator = User.objects.create_user(username="operador-firma@example.com")
        self.outsider = User.objects.create_user(username="sin-rol-firma@example.com")
        self.operator.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.warehouse = RemittanceWarehouse.objects.create(name="Bodega firma QA", is_default=True)
        self.supplier = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.SUPPLIER,
            nit="900000091",
            name="Proveedor privado QA",
        )
        self.customer = RemittanceParty.objects.create(
            party_type=RemittanceParty.PartyType.CUSTOMER,
            nit="830000091",
            name="Cliente público QA",
        )

    def tearDown(self):
        self.media_override.disable()
        self.private_media.cleanup()
        super().tearDown()

    def create_confirmed(self):
        self.client.force_authenticate(self.operator)
        created = self.client.post(
            "/api/facturacion/remisiones/",
            {
                "warehouse": self.warehouse.pk,
                "supplier": self.supplier.pk,
                "customer": self.customer.pk,
                "requester_name": "arley prueba",
                "requester_document": "123",
                "lines": [
                    {
                        "quantity": "2.000",
                        "original_description": "producto privado uno",
                        "usage_destination": "",
                        "supplier_sku": "NO-PUBLICAR",
                        "supplier_unit_cost": "15000.00",
                    },
                    {
                        "quantity": "1.000",
                        "original_description": "producto privado dos",
                        "usage_destination": "PLANTA",
                        "supplier_sku": "NO-PUBLICAR-2",
                        "supplier_unit_cost": "9000.00",
                    },
                ],
                "delivery": {"method": "PERSONAL_PICKUP", "notes": "Firma posterior"},
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        confirmed = self.client.post(
            f"/api/facturacion/remisiones/{created.data['id']}/confirmar/",
            {"expected_version": created.data["version"]},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        return Remittance.objects.get(pk=created.data["id"])

    def prepare_link(self, remittance):
        response = self.client.post(
            f"/api/facturacion/remisiones/{remittance.id}/compartir-whatsapp/",
            {"public_base_url": "http://127.0.0.1:5173"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["public_url"].rsplit("/", 1)[-1], response

    def valid_payload(self, remittance, *, idempotency_key=None):
        lines = list(remittance.lines.order_by("line_number"))
        return {
            "signerName": "cliente móvil",
            "idempotencyKey": str(idempotency_key or uuid.uuid4()),
            "signature": {"mimeType": "image/png", "base64": SIGNATURE_PNG},
            "allocations": [
                {"lineId": str(lines[0].public_id), "quantity": "1.000", "destination": "calle 80"},
                {"lineId": str(lines[0].public_id), "quantity": "1.000", "destination": "restaurante norte"},
                {"lineId": str(lines[1].public_id), "quantity": "1.000", "destination": "planta"},
            ],
        }

    def test_operator_prepares_generic_whatsapp_link_and_outsider_is_denied(self):
        remittance = self.create_confirmed()
        token, response = self.prepare_link(remittance)
        self.assertGreaterEqual(len(token), 40)
        self.assertTrue(response.data["whatsapp_url"].startswith("https://wa.me/?text="))
        self.assertNotIn("phone=", response.data["whatsapp_url"])

        self.client.force_authenticate(self.outsider)
        denied = self.client.post(
            f"/api/facturacion/remisiones/{remittance.id}/compartir-whatsapp/",
            {"public_base_url": "http://127.0.0.1:5173"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_public_form_is_anonymous_and_excludes_private_fields(self):
        remittance = self.create_confirmed()
        RemittanceUsageDestination.objects.create(customer=self.customer, value="calle 80")
        token, _ = self.prepare_link(remittance)
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/api/facturacion/remisiones/public/{token}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["clientName"], "CLIENTE PÚBLICO QA")
        self.assertIn("CALLE 80", response.data["destinations"])
        serialized = str(response.data).lower()
        for forbidden in ["supplier", "sku", "cost", "price", "invoice", "document"]:
            self.assertNotIn(forbidden, serialized)

    def test_recipient_can_allocate_sign_and_replay_idempotently(self):
        remittance = self.create_confirmed()
        token, _ = self.prepare_link(remittance)
        payload = self.valid_payload(remittance)
        self.client.force_authenticate(user=None)
        signed = self.client.post(
            f"/api/facturacion/remisiones/public/{token}/",
            payload,
            format="json",
        )
        self.assertEqual(signed.status_code, 201, signed.data)
        self.assertEqual(signed.data["signerName"], "CLIENTE MÓVIL")

        remittance.refresh_from_db()
        self.assertEqual(remittance.delivery_status, Remittance.DeliveryStatus.COMPLETED)
        self.assertEqual(remittance.delivery.recipient_name, "CLIENTE MÓVIL")
        self.assertEqual(remittance.recipient_acceptance.allocations.count(), 3)
        self.assertEqual(
            set(RemittanceUsageDestination.objects.values_list("value", flat=True)),
            {"CALLE 80", "RESTAURANTE NORTE", "PLANTA"},
        )
        replay = self.client.post(
            f"/api/facturacion/remisiones/public/{token}/",
            payload,
            format="json",
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(remittance.__class__.objects.get(pk=remittance.pk).recipient_acceptance.allocations.count(), 3)

    def test_allocation_must_match_each_line_and_signature_must_be_png(self):
        remittance = self.create_confirmed()
        token, _ = self.prepare_link(remittance)
        payload = self.valid_payload(remittance)
        payload["allocations"][0]["quantity"] = "0.500"
        self.client.force_authenticate(user=None)
        mismatch = self.client.post(
            f"/api/facturacion/remisiones/public/{token}/",
            payload,
            format="json",
        )
        self.assertEqual(mismatch.status_code, 400, mismatch.data)
        self.assertEqual(mismatch.data["code"], "ALLOCATION_MISMATCH")

        payload = self.valid_payload(remittance)
        payload["signature"] = {"mimeType": "image/jpeg", "base64": SIGNATURE_PNG}
        invalid_signature = self.client.post(
            f"/api/facturacion/remisiones/public/{token}/",
            payload,
            format="json",
        )
        self.assertEqual(invalid_signature.status_code, 400, invalid_signature.data)
        self.assertEqual(invalid_signature.data["code"], "INVALID_SIGNATURE")

    def test_expired_and_revoked_links_fail_closed(self):
        remittance = self.create_confirmed()
        expired_token = "e" * 43
        RemittanceShareLink.objects.create(
            remittance=remittance,
            token_hash=hash_token(expired_token),
            expires_at=timezone.now() - timedelta(minutes=1),
            created_by=self.operator,
        )
        self.client.force_authenticate(user=None)
        expired = self.client.get(f"/api/facturacion/remisiones/public/{expired_token}/")
        self.assertEqual(expired.status_code, 410, expired.data)
        self.assertEqual(expired.data["code"], "LINK_EXPIRED")

        revoked_token = "r" * 43
        RemittanceShareLink.objects.create(
            remittance=remittance,
            token_hash=hash_token(revoked_token),
            expires_at=timezone.now() + timedelta(days=1),
            revoked_at=timezone.now(),
            created_by=self.operator,
        )
        revoked = self.client.get(f"/api/facturacion/remisiones/public/{revoked_token}/")
        self.assertEqual(revoked.status_code, 410, revoked.data)
        self.assertEqual(revoked.data["code"], "LINK_REVOKED")

    def test_client_pdf_embeds_signature_and_excludes_private_accounting_data(self):
        remittance = self.create_confirmed()
        token, _ = self.prepare_link(remittance)

        unsigned = self.client.get(f"/api/facturacion/remisiones/{remittance.id}/documento/")
        self.assertEqual(unsigned.status_code, 200)
        self.assertEqual(unsigned["Content-Type"], "application/pdf")
        unsigned_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(unsigned.content)).pages)
        self.assertIn("Pendiente de firma", unsigned_text)

        self.client.force_authenticate(user=None)
        signed = self.client.post(
            f"/api/facturacion/remisiones/public/{token}/",
            self.valid_payload(remittance),
            format="json",
        )
        self.assertEqual(signed.status_code, 201, signed.data)
        document = self.client.get(f"/api/facturacion/remisiones/public/{token}/documento/")
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document["Content-Type"], "application/pdf")
        self.assertIn("inline", document["Content-Disposition"])

        reader = PdfReader(io.BytesIO(document.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertRegex(text, r"\d{2}/\d{2}/\d{4}")
        self.assertNotRegex(text, r"\d{1,2}:\d{2}")
        for expected in [
            remittance.number,
            "CLIENTE PÚBLICO QA",
            "830000091",
            "ARLEY PRUEBA",
            "Retira personalmente",
            "PRODUCTO PRIVADO UNO",
            "RESTAURANTE NORTE",
            "CLIENTE MÓVIL",
        ]:
            self.assertIn(expected, text)
        for forbidden in [
            "PROVEEDOR PRIVADO QA",
            "NO-PUBLICAR",
            "15000",
            "9000",
            "SKU",
            "SIIGO",
            "PRECIO",
            "COSTO",
        ]:
            self.assertNotIn(forbidden, text.upper())

        images = []
        for page in reader.pages:
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject") or {}
            images.extend(
                obj for obj in xobjects.values()
                if obj.get_object().get("/Subtype") == "/Image"
            )
        self.assertTrue(images, "La firma debe quedar embebida como imagen en el PDF")

        download = self.client.get(f"/api/facturacion/remisiones/public/{token}/documento/?download=1")
        self.assertIn("attachment", download["Content-Disposition"])

        self.client.force_authenticate(self.outsider)
        denied = self.client.get(f"/api/facturacion/remisiones/{remittance.id}/documento/")
        self.assertEqual(denied.status_code, 403)

    def test_public_pdf_link_expiry_fails_closed(self):
        remittance = self.create_confirmed()
        expired_token = "p" * 43
        RemittanceShareLink.objects.create(
            remittance=remittance,
            token_hash=hash_token(expired_token),
            expires_at=timezone.now() - timedelta(minutes=1),
            created_by=self.operator,
        )
        self.client.force_authenticate(user=None)
        expired = self.client.get(f"/api/facturacion/remisiones/public/{expired_token}/documento/")
        self.assertEqual(expired.status_code, 410, expired.data)
        self.assertEqual(expired.data["code"], "LINK_EXPIRED")
