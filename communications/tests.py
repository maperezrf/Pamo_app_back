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
from .serializers import draft_payload


User = get_user_model()


@override_settings(
    EXTERNAL_WRITES_ENABLED=False,
    MESSAGING_EXTERNAL_WRITES_ENABLED=False,
    PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED=False,
    PAMO_WHATSAPP_PROVIDER="mock",
    PAMO_WHATSAPP_AUTO_PREPARE_ENABLED=True,
    PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED=True,
    PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED=True,
    PAMO_WHATSAPP_DEPLOYMENT_TIER="local",
    PAMO_WHATSAPP_PILOT_RECIPIENT="573000004936",
    PAMO_WHATSAPP_PILOT_RECIPIENT_NAME="Mauricio QA",
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
            source_snapshot={
                "source_channel": "shopify",
                "shopify_warehouse_location_id": self.warehouse.external_id,
                "shopify_warehouse_name": self.warehouse.name,
            },
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

    @staticmethod
    def interactive_choice(payload, title):
        choices = list(payload.get("buttons") or [])
        for section in payload.get("sections") or []:
            choices.extend(section.get("rows") or [])
        return next(item["id"] for item in choices if item["title"] == title)

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
        self.assertEqual(payload["pilotRecipientMasked"], "••••4936")
        self.assertEqual(
            payload["internalRecipients"],
            [
                {
                    "name": "Mauricio QA",
                    "phoneMasked": "••••4936",
                    "active": True,
                    "configured": True,
                }
            ],
        )
        self.assertNotIn("573000004936", json.dumps(payload))

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
        from communications.orders_contract import create_workflow_draft

        draft_object, _ = create_workflow_draft(
            shipment=self.shipment,
            contact=self.contact,
            message_kind="guide_delivery",
            actor="qa.whatsapp@pamo.test",
        )
        draft = draft_payload(draft_object)
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
        self.assertEqual(stored.state, "failed")
        self.assertEqual(stored.attempt_count, 1)

    def test_meta_401_blocks_connection_and_prevents_retry_loop(self):
        from communications.providers import MetaWhatsAppClient, WhatsAppProviderError
        from communications.services import dispatch_outbox

        class UnauthorizedResponse:
            status_code = 401

            @staticmethod
            def json():
                return {}

        class OneCallSession:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return UnauthorizedResponse()

        config = WhatsAppChannelConfig.objects.create(
            slug="primary", connection_state="ready", active=True
        )
        draft = self.create_draft()
        outbox = self.approve_and_enqueue(draft["id"])
        session = OneCallSession()
        provider = MetaWhatsAppClient(session=session)
        with self.assertRaises(WhatsAppProviderError) as first:
            dispatch_outbox(outbox_id=outbox["id"], client=provider)
        self.assertEqual(first.exception.code, "META_TOKEN_INVALID")
        config.refresh_from_db()
        self.assertEqual(config.connection_state, "blocked")
        self.assertFalse(config.active)

        with self.assertRaises(WhatsAppProviderError) as second:
            dispatch_outbox(outbox_id=outbox["id"], client=provider)
        self.assertEqual(second.exception.code, "META_CONNECTION_BLOCKED")
        stored = WhatsAppOutbox.objects.get(id=outbox["id"])
        self.assertEqual(stored.attempt_count, 1)
        self.assertEqual(session.calls, 1)

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

    def test_automatic_pilot_fans_out_per_contact_without_supplier_phones(self):
        from communications.orders_contract import auto_prepare_new_shipments

        second = MessagingContact.objects.create(
            config=self.contact.config,
            name="Segundo contacto",
            phone="573000000099",
            active=True,
        )
        first = auto_prepare_new_shipments([self.shipment])
        replay = auto_prepare_new_shipments([self.shipment])
        self.assertEqual(first["created"], 2)
        self.assertEqual(first["dispatched"], 2)
        self.assertEqual(replay["created"], 0)
        self.assertEqual(replay["reused"], 2)
        self.assertEqual(WhatsAppDraft.objects.count(), 2)
        self.assertEqual(WhatsAppOutbox.objects.count(), 2)
        self.assertEqual(
            set(WhatsAppDraft.objects.values_list("recipient_phone", flat=True)),
            {"573000004936"},
        )
        serialized = json.dumps(list(WhatsAppDraft.objects.values()), default=str)
        self.assertNotIn(self.contact.phone, serialized)
        self.assertNotIn(second.phone, serialized)

    def test_automatic_pilot_rejects_name_only_or_mismatched_shopify_location(self):
        from communications.orders_contract import auto_prepare_new_shipments

        self.shipment.source_snapshot = {
            "source_channel": "shopify",
            "shopify_warehouse_name": self.warehouse.name,
        }
        self.shipment.save(update_fields=["source_snapshot"])
        result = auto_prepare_new_shipments([self.shipment])
        self.assertEqual(result["skippedUntrustedWarehouse"], 1)
        self.assertEqual(WhatsAppDraft.objects.count(), 0)

    def _signed_inbound(
        self, *, context_id, inbound_id, reply_id="", text="", reply_kind="list"
    ):
        value = {
            "metadata": {"phone_number_id": "phone-local-qa"},
            "messages": [
                {
                    "from": "573000004936",
                    "id": inbound_id,
                    "timestamp": "1787860000",
                    "context": {"id": context_id},
                    **(
                        {
                            "type": "interactive",
                            "interactive": {
                                "type": f"{reply_kind}_reply",
                                f"{reply_kind}_reply": {"id": reply_id},
                            },
                        }
                        if reply_id
                        else {"type": "text", "text": {"body": text}}
                    ),
                }
            ],
        }
        payload = {
            "entry": [
                {
                    "id": "waba-local-qa",
                    "changes": [{"value": value}],
                }
            ]
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"app-secret-local-qa", raw, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            "/api/communications/whatsapp/webhook/meta/",
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
    )
    def test_inbound_response_is_bound_to_exact_context_and_first_action_wins(self):
        from communications.orders_contract import auto_prepare_new_shipments

        auto_prepare_new_shipments([self.shipment])
        initial = WhatsAppDraft.objects.get(message_kind="supplier_order")
        initial_outbox = initial.outbox
        report_issue = self.interactive_choice(
            initial.interactive_payload, "Reportar novedad"
        )
        first = self._signed_inbound(
            context_id=initial_outbox.provider_message_id,
            inbound_id="wamid.inbound.issue",
            reply_id=report_issue,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["processed"], 1)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.supplier_state, "issue_reported")
        self.assertTrue(WhatsAppDraft.objects.filter(message_kind="novelty_menu").exists())

        request_guide = self.interactive_choice(
            initial.interactive_payload, "Listo para despacho"
        )
        conflict = self._signed_inbound(
            context_id=initial_outbox.provider_message_id,
            inbound_id="wamid.inbound.conflict",
            reply_id=request_guide,
        )
        self.assertEqual(conflict.json()["processed"], 1)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.supplier_state, "issue_reported")
        review = self.shipment.supplier_response_events.get(
            provider_event_id="wamid.inbound.conflict"
        )
        self.assertEqual(review.result, "review")
        self.assertEqual(review.details["reason"], "conflicts_with_first_primary_response")

        wrong_context = self._signed_inbound(
            context_id="mock-message-does-not-exist",
            inbound_id="wamid.inbound.wrong-context",
            reply_id=request_guide,
        )
        self.assertEqual(wrong_context.json()["rejected"], 1)

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
    )
    def test_new_order_to_signed_novelty_flow_is_additive_visible_and_idempotent(self):
        from communications.orders_contract import auto_prepare_new_shipments

        auto_prepare_new_shipments([self.shipment])
        initial = WhatsAppDraft.objects.get(message_kind="supplier_order")
        report_issue = self.interactive_choice(
            initial.interactive_payload, "Reportar novedad"
        )
        opened = self._signed_inbound(
            context_id=initial.outbox.provider_message_id,
            inbound_id="wamid.e2e.open",
            reply_id=report_issue,
        )
        self.assertEqual(opened.json()["processed"], 1)

        category_menu = WhatsAppDraft.objects.get(message_kind="novelty_menu")
        damaged = self.interactive_choice(
            category_menu.interactive_payload, "Producto averiado"
        )
        categorized = self._signed_inbound(
            context_id=category_menu.outbox.provider_message_id,
            inbound_id="wamid.e2e.category",
            reply_id=damaged,
        )
        self.assertEqual(categorized.json()["processed"], 1)

        sku_menu = WhatsAppDraft.objects.get(message_kind="issue_sku_menu")
        sku_choice = self.interactive_choice(sku_menu.interactive_payload, "SKU-QA")
        selected = self._signed_inbound(
            context_id=sku_menu.outbox.provider_message_id,
            inbound_id="wamid.e2e.sku",
            reply_id=sku_choice,
        )
        self.assertEqual(selected.json()["processed"], 1)

        quantity_prompt = WhatsAppDraft.objects.get(message_kind="issue_quantity_prompt")
        completed = self._signed_inbound(
            context_id=quantity_prompt.outbox.provider_message_id,
            inbound_id="wamid.e2e.quantity",
            text="1",
        )
        self.assertEqual(completed.json()["processed"], 1)
        confirmation = WhatsAppDraft.objects.get(message_kind="novelty_confirmation")
        self.assertEqual(confirmation.outbox.state, "sent")
        self.assertIn("19346", confirmation.rendered_body)
        self.assertNotIn(self.order.customer_name, confirmation.rendered_body)

        detail = self.client.get(f"/api/pedidos/{self.order.id}/")
        self.assertEqual(detail.status_code, 200)
        shipment = detail.json()["shipments"][0]
        self.assertEqual(len(shipment["supplier_response_events"]), 4)
        self.assertEqual(shipment["novelties"][0]["category"], "supplier_damage")
        self.assertEqual(shipment["novelties"][0]["detail_state"], "complete")
        self.assertEqual(
            shipment["novelties"][0]["affected_items"][0]["shipmentItemId"],
            str(self.shipment.shipment_items.get().id),
        )
        self.assertFalse(WhatsAppDraft.objects.filter(message_kind="guide_delivery").exists())

        replay = self._signed_inbound(
            context_id=quantity_prompt.outbox.provider_message_id,
            inbound_id="wamid.e2e.quantity",
            text="1",
        )
        self.assertEqual(replay.json()["duplicates"], 1)
        self.assertEqual(self.shipment.supplier_response_events.count(), 4)

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
    )
    def test_stockout_with_multiple_skus_requires_item_and_quantity(self):
        from communications.orders_contract import auto_prepare_new_shipments

        other_item = OrderItem.objects.create(
            order=self.order,
            external_id="qa-whatsapp-item-2",
            sku="SKU-QA-2",
            name="Segundo artículo QA",
            quantity=3,
            unit_price=Decimal("10000"),
            line_total=Decimal("30000"),
        )
        ShipmentItem.objects.create(
            shipment=self.shipment, order_item=other_item, quantity=3
        )
        auto_prepare_new_shipments([self.shipment])
        initial = WhatsAppDraft.objects.get(message_kind="supplier_order")
        stockout = self.interactive_choice(initial.interactive_payload, "Agotado")
        opened = self._signed_inbound(
            context_id=initial.outbox.provider_message_id,
            inbound_id="wamid.stockout.open",
            reply_id=stockout,
        )
        self.assertEqual(opened.json()["processed"], 1)
        novelty = self.shipment.novelties.get()
        self.assertEqual(novelty.detail_state, "awaiting_item")
        self.assertEqual(novelty.affected_items, [])

        sku_menu = WhatsAppDraft.objects.get(message_kind="issue_sku_menu")
        selected_sku = self.interactive_choice(sku_menu.interactive_payload, "SKU-QA-2")
        self._signed_inbound(
            context_id=sku_menu.outbox.provider_message_id,
            inbound_id="wamid.stockout.sku",
            reply_id=selected_sku,
        )
        novelty.refresh_from_db()
        self.assertEqual(novelty.detail_state, "awaiting_quantity")
        self.assertEqual(len(novelty.affected_items), 1)
        self.assertEqual(novelty.affected_items[0]["sku"], "SKU-QA-2")

        prompt = WhatsAppDraft.objects.get(message_kind="issue_quantity_prompt")
        self._signed_inbound(
            context_id=prompt.outbox.provider_message_id,
            inbound_id="wamid.stockout.quantity",
            text="2",
        )
        novelty.refresh_from_db()
        self.assertEqual(novelty.detail_state, "complete")
        self.assertEqual(novelty.affected_items[0]["affectedQuantity"], 2)
        self.assertEqual(novelty.affected_items[0]["scope"], "partial")
        self.assertEqual(
            {item["sku"] for item in novelty.affected_items}, {"SKU-QA-2"}
        )

    @override_settings(
        META_APP_SECRET="app-secret-local-qa",
        META_WABA_ID="waba-local-qa",
        META_PHONE_NUMBER_ID="phone-local-qa",
        PAMO_WHATSAPP_GUIDE_AUTO_SEND_ENABLED=True,
    )
    def test_requested_guide_waits_and_dispatches_when_pdf_appears(self):
        from communications.orders_contract import (
            auto_prepare_new_shipments,
            dispatch_requested_guides,
        )

        auto_prepare_new_shipments([self.shipment])
        initial = WhatsAppDraft.objects.get(message_kind="supplier_order")
        request_guide = self.interactive_choice(
            initial.interactive_payload, "Listo para despacho"
        )
        response = self._signed_inbound(
            context_id=initial.outbox.provider_message_id,
            inbound_id="wamid.inbound.guide-request",
            reply_id=request_guide,
        )
        self.assertEqual(response.json()["processed"], 1)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.guide_delivery_state, "requested")
        self.assertFalse(WhatsAppDraft.objects.filter(message_kind="guide_delivery").exists())

        content = b"%PDF-1.4\nrequested guide"
        ShipmentDocument.objects.create(
            shipment=self.shipment,
            file=SimpleUploadedFile("guia.pdf", content, content_type="application/pdf"),
            original_name="guia.pdf",
            mime_type="application/pdf",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            uploaded_by="canonical-read-only-import",
        )
        result = dispatch_requested_guides([self.shipment])
        self.assertEqual(result["prepared"], 1)
        self.assertEqual(result["dispatched"], 1)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.guide_delivery_state, "sent")
        guide = WhatsAppDraft.objects.get(message_kind="guide_delivery")
        self.assertEqual(guide.recipient_phone, "573000004936")
        self.assertEqual(guide.outbox.state, "sent")

    @override_settings(PAMO_WHATSAPP_INTERNAL_COPY_FROM="2026-01-01T00:00:00-05:00")
    def test_internal_copy_is_one_per_order_and_survives_restart_without_duplicate(self):
        from communications.internal_copies import auto_send_internal_order_copies

        second_warehouse = WarehouseLocation.objects.create(
            external_id="qa-whatsapp-second", name="Segunda bodega"
        )
        second_shipment = Shipment.objects.create(
            order=self.order,
            external_id="qa-whatsapp-second-shipment",
            warehouse=second_warehouse,
            warehouse_name=second_warehouse.name,
            logistics_state="without_guide",
        )
        ShipmentItem.objects.create(
            shipment=second_shipment, order_item=self.item, quantity=1
        )
        first = auto_send_internal_order_copies([self.order])
        replay = auto_send_internal_order_copies([self.order])
        self.assertEqual(first["created"], 1)
        self.assertEqual(first["dispatched"], 1)
        self.assertEqual(replay["created"], 0)
        self.assertEqual(replay["reused"], 1)
        draft = WhatsAppDraft.objects.get(message_kind="internal_order_copy")
        self.assertEqual(draft.outbox.attempt_count, 1)
        self.assertEqual(draft.rendered_body.count("Despacho ·"), 2)
        self.assertIn("SKU SKU-QA · Artículo QA · 1 unidad(es)", draft.rendered_body)

    @override_settings(PAMO_WHATSAPP_INTERNAL_COPY_FROM="2099-01-01T00:00:00-05:00")
    def test_internal_copy_checkpoint_blocks_historical_or_restart_backfill(self):
        from communications.internal_copies import auto_send_internal_order_copies

        result = auto_send_internal_order_copies([self.order])
        self.assertEqual(result["skippedBeforeCheckpoint"], 1)
        self.assertFalse(WhatsAppDraft.objects.filter(message_kind="internal_order_copy").exists())

    @override_settings(
        PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED=False,
        PAMO_WHATSAPP_INTERNAL_COPY_FROM="2026-01-01T00:00:00-05:00",
    )
    def test_internal_copy_automation_is_off_by_default_gate(self):
        from communications.internal_copies import auto_send_internal_order_copies

        result = auto_send_internal_order_copies([self.order])
        self.assertEqual(result["skippedAutomationDisabled"], 1)
        self.assertFalse(WhatsAppDraft.objects.filter(message_kind="internal_order_copy").exists())

    @override_settings(PAMO_WHATSAPP_INTERNAL_COPY_FROM="2026-01-01T00:00:00-05:00")
    def test_internal_copy_failure_does_not_escape_and_retry_does_not_duplicate(self):
        from communications.internal_copies import auto_send_internal_order_copies
        from communications.providers import WhatsAppProviderError

        class FailingClient:
            def send_message(self, **kwargs):
                raise WhatsAppProviderError("TEMPORARY_QA_FAILURE", "Fallo simulado.")

        failed = auto_send_internal_order_copies(
            [self.order], client_factory=lambda recipient: FailingClient()
        )
        self.assertEqual(len(failed["recipientFailures"]), 1)
        draft = WhatsAppDraft.objects.get(message_kind="internal_order_copy")
        self.assertEqual(draft.outbox.state, "failed")
        self.assertEqual(draft.outbox.attempt_count, 1)

        retried = auto_send_internal_order_copies([self.order])
        self.assertEqual(retried["created"], 0)
        self.assertEqual(retried["reused"], 1)
        self.assertEqual(retried["dispatched"], 1)
        draft.refresh_from_db()
        self.assertEqual(draft.outbox.attempt_count, 2)
        self.assertEqual(draft.outbox.state, "sent")
