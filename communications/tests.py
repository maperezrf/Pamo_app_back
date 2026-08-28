import hashlib
import hmac
import json
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from pedidos.models import (
    MessagingConfig,
    MessagingContact,
    Order,
    OrderItem,
    Shipment,
    ShipmentDocument,
    ShipmentItem,
    WarehouseLocation,
)

from .models import (
    WhatsAppChannelConfig,
    WhatsAppDraft,
    WhatsAppOutbox,
    WhatsAppWebhookEvent,
)


User = get_user_model()


@override_settings(
    EXTERNAL_WRITES_ENABLED=False,
    MESSAGING_EXTERNAL_WRITES_ENABLED=False,
    PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED=False,
    PAMO_WHATSAPP_PROVIDER="mock",
)
class WhatsAppPlatformTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Operaciones")
        self.user = User.objects.create_user(
            username="qa.whatsapp@pamo.test", email="qa.whatsapp@pamo.test"
        )
        self.user.groups.add(group)
        self.warehouse = WarehouseLocation.objects.create(
            external_id="qa-whatsapp-baru", name="Barú", reference="BARU"
        )
        self.other_warehouse = WarehouseLocation.objects.create(
            external_id="qa-whatsapp-provider", name="Proveedores", reference="PROV"
        )
        self.order = Order.objects.create(
            channel="shopify",
            external_id="qa-whatsapp-order",
            visible_id="19346",
            placed_at=timezone.now() - timedelta(minutes=20),
            customer_name="Cliente QA",
            grand_total=Decimal("120000"),
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            external_id="qa-whatsapp-item",
            sku="SKU-QA",
            name="Artículo QA",
            quantity=1,
            unit_price=Decimal("120000"),
            line_total=Decimal("120000"),
        )
        self.shipment = Shipment.objects.create(
            order=self.order,
            external_id="qa-whatsapp-shipment",
            warehouse=self.warehouse,
            warehouse_name=self.warehouse.name,
            tracking_number="GUIA-QA-19346",
            logistics_state="guide_without_tracking",
        )
        ShipmentItem.objects.create(
            shipment=self.shipment, order_item=self.item, quantity=1
        )
        config = MessagingConfig.objects.create(warehouse=self.warehouse)
        self.contact = MessagingContact.objects.create(
            config=config, name="Bodega QA", phone="573000000001", active=True
        )
        other_config = MessagingConfig.objects.create(warehouse=self.other_warehouse)
        self.other_contact = MessagingContact.objects.create(
            config=other_config, name="Proveedor equivocado", phone="573000000002"
        )
        self.client.force_login(self.user)

    def selection(self, contact=None):
        return {
            "shipment_id": str(self.shipment.id),
            "contact_id": str((contact or self.contact).id),
        }

    def create_draft(self):
        response = self.client.post(
            "/api/communications/whatsapp/drafts/",
            data={"selections": [self.selection()]},
            content_type="application/json",
        )
        self.assertIn(response.status_code, {200, 201})
        return response.json()["drafts"][0]

    def test_settings_start_local_unlinked_and_without_secrets(self):
        response = self.client.get("/api/communications/whatsapp/settings/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["config"]["connectionState"], "not_linked")
        self.assertTrue(payload["localOnly"])
        self.assertFalse(payload["secretsStored"])
        self.assertEqual(payload["externalWrites"], 0)

    def test_settings_save_only_non_secret_asset_metadata(self):
        response = self.client.put(
            "/api/communications/whatsapp/settings/",
            data={
                "provider": "meta_cloud_api",
                "partnerName": "VAMBE",
                "displayName": "Pamo Colombia",
                "businessId": "2002307326720070",
                "wabaId": "368142023060178",
                "phoneNumberId": "383534111518991",
                "displayPhoneNumber": "+1 555-708-1978",
                "connectionState": "observed",
                "qualityRating": "high",
                "webhookState": "not_configured",
                "active": False,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["externalWrites"], 0)
        stored = WhatsAppChannelConfig.objects.get(slug="primary")
        self.assertEqual(stored.phone_number_id, "383534111518991")
        self.assertFalse(stored.active)

    def test_settings_reject_secret_fields(self):
        response = self.client.put(
            "/api/communications/whatsapp/settings/",
            data={"systemUserToken": "never-store-this"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WhatsAppChannelConfig.objects.count(), 0)

    def approve_and_enqueue(self, draft_id):
        approved = self.client.post(
            f"/api/communications/whatsapp/drafts/{draft_id}/approve/"
        )
        self.assertEqual(approved.status_code, 200)
        queued = self.client.post(
            f"/api/communications/whatsapp/drafts/{draft_id}/enqueue/"
        )
        self.assertIn(queued.status_code, {200, 201})
        return queued.json()["outbox"]

    def test_capabilities_fail_closed_and_keep_manual_fallback(self):
        response = self.client.get("/api/communications/whatsapp/capabilities/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["mockMode"])
        self.assertFalse(payload["externalWritesEnabled"])
        self.assertTrue(payload["humanApprovalRequired"])
        self.assertTrue(payload["manualWhatsAppWebFallback"])
        self.assertEqual(payload["externalWrites"], 0)

    def test_recipient_options_are_scoped_to_each_shipment_warehouse(self):
        response = self.client.post(
            "/api/communications/whatsapp/recipients/",
            data={"shipment_ids": [str(self.shipment.id)]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        contacts = response.json()["shipments"][0]["contacts"]
        self.assertEqual([item["id"] for item in contacts], [str(self.contact.id)])
        self.assertNotIn(self.contact.phone, json.dumps(response.json()))

    def test_draft_preview_is_idempotent_and_requires_explicit_contact(self):
        first = self.client.post(
            "/api/communications/whatsapp/drafts/",
            data={"selections": [self.selection()]},
            content_type="application/json",
        )
        second = self.client.post(
            "/api/communications/whatsapp/drafts/",
            data={"selections": [self.selection()]},
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["drafts"][0]["id"], second.json()["drafts"][0]["id"])
        self.assertEqual(second.json()["reused"], 1)
        self.assertEqual(WhatsAppDraft.objects.count(), 1)

    def test_contact_from_another_warehouse_is_rejected(self):
        response = self.client.post(
            "/api/communications/whatsapp/drafts/",
            data={"selections": [self.selection(self.other_contact)]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WhatsAppDraft.objects.count(), 0)

    def test_outbox_cannot_be_created_before_human_approval(self):
        draft = self.create_draft()
        response = self.client.post(
            f"/api/communications/whatsapp/drafts/{draft['id']}/enqueue/"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(WhatsAppOutbox.objects.count(), 0)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_mock_uploads_pdf_by_media_id_and_dispatch_is_idempotent(self):
        content = b"%PDF-1.4\nwhatsapp local qa"
        ShipmentDocument.objects.create(
            shipment=self.shipment,
            file=SimpleUploadedFile("guia-19346.pdf", content, content_type="application/pdf"),
            original_name="guia-19346.pdf",
            mime_type="application/pdf",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            uploaded_by="qa.whatsapp@pamo.test",
        )
        draft = self.create_draft()
        self.assertTrue(draft["document"]["available"])
        outbox = self.approve_and_enqueue(draft["id"])
        endpoint = f"/api/communications/whatsapp/outbox/{outbox['id']}/dispatch/"
        first = self.client.post(endpoint)
        second = self.client.post(endpoint)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["simulation"])
        self.assertEqual(first.json()["externalWrites"], 0)
        self.assertTrue(first.json()["outbox"]["hasMediaId"])
        self.assertTrue(first.json()["outbox"]["hasProviderMessageId"])
        self.assertFalse(second.json()["dispatched"])
        stored = WhatsAppOutbox.objects.get(id=outbox["id"])
        self.assertEqual(stored.attempt_count, 1)
        self.assertEqual(stored.attempts.count(), 1)

    @override_settings(PAMO_WHATSAPP_PROVIDER="meta")
    def test_meta_dispatch_remains_blocked_with_all_gates_off(self):
        draft = self.create_draft()
        outbox = self.approve_and_enqueue(draft["id"])
        response = self.client.post(
            f"/api/communications/whatsapp/outbox/{outbox['id']}/dispatch/"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "WHATSAPP_EXTERNAL_WRITES_DISABLED")
        stored = WhatsAppOutbox.objects.get(id=outbox["id"])
        self.assertEqual(stored.state, "pending")
        self.assertEqual(stored.attempt_count, 0)

    @override_settings(META_VERIFY_TOKEN="verify-local-qa")
    def test_webhook_get_verifies_without_exposing_token(self):
        response = self.client.get(
            "/api/communications/whatsapp/webhook/meta/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-local-qa",
                "hub.challenge": "12345",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"12345")

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
    )
    def test_signed_webhook_updates_status_once_and_deduplicates(self):
        draft = self.create_draft()
        outbox_data = self.approve_and_enqueue(draft["id"])
        dispatch = self.client.post(
            f"/api/communications/whatsapp/outbox/{outbox_data['id']}/dispatch/"
        ).json()["outbox"]
        outbox = WhatsAppOutbox.objects.get(id=outbox_data["id"])
        self.assertTrue(dispatch["hasProviderMessageId"])
        payload = {
            "entry": [
                {
                    "id": "waba-local-qa",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "phone-local-qa"},
                                "statuses": [
                                    {
                                        "id": outbox.provider_message_id,
                                        "status": "delivered",
                                        "timestamp": "1787860000",
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"app-secret-local-qa", raw, hashlib.sha256
        ).hexdigest()
        endpoint = "/api/communications/whatsapp/webhook/meta/"
        first = self.client.post(
            endpoint,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        second = self.client.post(
            endpoint,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["processed"], 1)
        self.assertEqual(second.json()["duplicates"], 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.state, "delivered")
        event = WhatsAppWebhookEvent.objects.get()
        self.assertTrue(event.signature_valid)
        self.assertEqual(event.duplicate_count, 1)
        self.assertFalse(hasattr(event, "payload"))

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
    )
    def test_webhook_rejects_invalid_signature_and_wrong_phone(self):
        endpoint = "/api/communications/whatsapp/webhook/meta/"
        invalid = self.client.post(
            endpoint,
            data=b"{}",
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalid",
        )
        self.assertEqual(invalid.status_code, 403)
        payload = {
            "entry": [
                {
                    "id": "waba-local-qa",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "otro-phone"},
                                "statuses": [],
                            }
                        }
                    ],
                }
            ]
        }
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(
            b"app-secret-local-qa", raw, hashlib.sha256
        ).hexdigest()
        wrong_phone = self.client.post(
            endpoint,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        self.assertEqual(wrong_phone.status_code, 403)
        self.assertEqual(wrong_phone.json()["code"], "PHONE_NUMBER_ID_MISMATCH")
        self.assertEqual(WhatsAppWebhookEvent.objects.count(), 0)
