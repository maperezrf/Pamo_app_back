import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    LogisticsAudit,
    ManualFollowup,
    MessagingConfig,
    MessagingContact,
    Order,
    OrderItem,
    Shipment,
    ShipmentNovelty,
    SupplierResponseEvent,
    ShipmentItem,
    WarehouseLocation,
)
from .functions.supplier_responses import (
    SupplierResponseError,
    apply_supplier_novelty_category,
    apply_supplier_novelty_detail,
    apply_supplier_response,
)


User = get_user_model()


class OrdersAPITests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Operaciones")
        self.user = User.objects.create_user(
            username="qa.orders@pamo.test", email="qa.orders@pamo.test"
        )
        self.user.groups.add(group)
        self.location = WarehouseLocation.objects.create(
            external_id="qa-baru", name="Barú", reference="BARU"
        )
        self.provider_location = WarehouseLocation.objects.create(
            external_id="qa-proveedores", name="Proveedores", reference="PROV"
        )
        self.order = Order.objects.create(
            channel="shopify",
            external_id="qa-shopify-19335",
            visible_id="19335",
            placed_at=timezone.now() - timedelta(hours=1),
            customer_name="Cliente QA",
            grand_total=Decimal("1139718"),
        )
        self.item_a = OrderItem.objects.create(
            order=self.order,
            external_id="item-a",
            sku="8844",
            name="Artículo Barú",
            quantity=3,
            unit_price=Decimal("200000"),
            line_total=Decimal("600000"),
        )
        self.item_b = OrderItem.objects.create(
            order=self.order,
            external_id="item-b",
            sku="GV-L025",
            name="Artículo proveedor",
            quantity=1,
            unit_price=Decimal("539718"),
            line_total=Decimal("539718"),
        )
        self.shipment_a = Shipment.objects.create(
            order=self.order,
            external_id="shipment-a",
            warehouse=self.location,
            warehouse_name="Barú",
            logistics_state="without_guide",
        )
        self.shipment_b = Shipment.objects.create(
            order=self.order,
            external_id="shipment-b",
            warehouse=self.provider_location,
            warehouse_name="Proveedores",
            logistics_state="without_guide",
        )
        ShipmentItem.objects.create(
            shipment=self.shipment_a, order_item=self.item_a, quantity=3
        )
        ShipmentItem.objects.create(
            shipment=self.shipment_b, order_item=self.item_b, quantity=1
        )

    def login(self):
        self.client.force_login(self.user)

    def test_orders_requires_authenticated_operator(self):
        response = self.client.get("/api/pedidos/")
        self.assertEqual(response.status_code, 403)

    def test_multiwarehouse_order_is_one_row_with_stacked_values(self):
        self.login()
        response = self.client.get("/api/pedidos/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["orders"]), 1)
        self.assertEqual(payload["orders"][0]["channel_order_id"], "19335")
        self.assertEqual(payload["orders"][0]["warehouses"], ["Barú", "Proveedores"])
        self.assertEqual(payload["orders"][0]["shipment_count"], 2)
        self.assertEqual(
            payload["orders"][0]["label_statuses"],
            ["pending_provider", "pending_provider"],
        )
        self.assertEqual(payload["externalWrites"], 0)

    def test_order_detail_explains_non_printable_fulfillment_label(self):
        self.shipment_a.source_snapshot = {
            "canonicalImport": True,
            "label_availability": {
                "status": "not_printable",
                "reason": "MERCADOLIBRE_FULFILLMENT_NO_SELLER_LABEL",
                "checked_at": None,
            },
        }
        self.shipment_a.save()
        self.login()

        response = self.client.get(f"/api/pedidos/{self.order.id}/")

        self.assertEqual(response.status_code, 200)
        shipment = next(
            item for item in response.json()["shipments"]
            if item["id"] == str(self.shipment_a.id)
        )
        self.assertFalse(shipment["has_document"])
        self.assertEqual(shipment["label_status"], "not_printable")
        self.assertEqual(
            shipment["label_status_reason"],
            "MERCADOLIBRE_FULFILLMENT_NO_SELLER_LABEL",
        )

    def test_search_by_sku_finds_order(self):
        self.login()
        response = self.client.get("/api/pedidos/?search=GV-L025")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

    def test_filter_options_do_not_repeat_channels(self):
        Order.objects.create(
            channel="shopify",
            external_id="qa-shopify-second",
            visible_id="19336",
            placed_at=timezone.now(),
            customer_name="Segundo pedido QA",
            grand_total=Decimal("100000"),
        )
        self.login()

        response = self.client.get("/api/pedidos/filter-options/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channels"], ["shopify"])

    def test_locations_identify_shopify_and_local_records_without_deleting_history(self):
        shopify_location = WarehouseLocation.objects.create(
            external_id="gid://shopify/Location/123",
            name="Barú",
        )
        self.login()

        response = self.client.get("/api/pedidos/locations/")

        self.assertEqual(response.status_code, 200)
        by_id = {item["id"]: item for item in response.json()["locations"]}
        self.assertEqual(by_id[str(self.location.id)]["source"], "local")
        self.assertEqual(by_id[str(shopify_location.id)]["source"], "shopify")
        self.assertIn("Shopify", by_id[str(shopify_location.id)]["display_name"])

    def test_sodimac_via_shopify_keeps_source_link_and_filters_by_business_origin(self):
        self.order.source_snapshot = {
            "canonicalImport": True,
            "business_origin": "sodimac",
            "business_origin_via": "shopify",
            "business_origin_confidence": "explicit_source_email_marker",
        }
        self.order.source_url = "https://admin.shopify.com/store/example/orders/1"
        self.order.save()
        Order.objects.create(
            channel="shopify",
            external_id="qa-shopify-native",
            visible_id="19336",
            placed_at=timezone.now(),
            customer_name="Pedido Shopify QA",
            grand_total=Decimal("100000"),
            source_snapshot={
                "canonicalImport": True,
                "business_origin": "shopify",
                "business_origin_via": "shopify",
            },
        )
        self.login()

        sodimac = self.client.get("/api/pedidos/?channel=sodimac").json()
        shopify = self.client.get("/api/pedidos/?channel=shopify").json()
        options = self.client.get("/api/pedidos/filter-options/").json()

        self.assertEqual(sodimac["total"], 1)
        self.assertEqual(sodimac["orders"][0]["business_origin"], "sodimac")
        self.assertEqual(sodimac["orders"][0]["business_origin_via"], "shopify")
        self.assertEqual(
            sodimac["orders"][0]["channel_order_url"],
            "https://admin.shopify.com/store/example/orders/1",
        )
        self.assertEqual(shopify["total"], 1)
        self.assertEqual(options["channels"], ["shopify", "sodimac"])

    def test_combined_guide_filter_and_separate_filter(self):
        self.shipment_b.tracking_number = "GUIA-1"
        self.shipment_b.logistics_state = "guide_without_tracking"
        self.shipment_b.save()
        Order.objects.create(
            channel="shopify",
            external_id="qa-shopify-without-shipment",
            visible_id="19334",
            placed_at=timezone.now(),
            customer_name="Pedido sin despacho QA",
            grand_total=Decimal("50000"),
        )
        self.login()

        missing = self.client.get("/api/pedidos/?guide=missing").json()
        untracked = self.client.get(
            "/api/pedidos/?guide=present_without_tracking"
        ).json()
        combined = self.client.get(
            "/api/pedidos/?guide=missing_or_without_tracking"
        ).json()

        self.assertEqual(missing["total"], 2)
        self.assertEqual(untracked["total"], 1)
        self.assertEqual(combined["total"], 2)

    def test_manual_warehouse_override_is_audited_and_versioned(self):
        self.login()
        response = self.client.patch(
            f"/api/pedidos/shipments/{self.shipment_a.id}/",
            data={
                "version": self.shipment_a.version,
                "warehouse_location_id": self.provider_location.id,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.warehouse, self.provider_location)
        self.assertTrue(self.shipment_a.warehouse_locked)
        self.assertEqual(self.shipment_a.warehouse_assignment_source, "manual")
        self.assertEqual(LogisticsAudit.objects.filter(field="warehouse").count(), 1)
        self.assertIn(
            f"warehouse_location_id={self.provider_location.id}",
            LogisticsAudit.objects.get(field="warehouse").detail,
        )

    def test_unassigned_filter_and_overview_count_orders_needing_camila(self):
        order = Order.objects.create(
            channel="shopify",
            external_id="qa-shopify-unassigned",
            visible_id="19337",
            placed_at=timezone.now(),
            customer_name="Pedido sin bodega QA",
            grand_total=Decimal("50000"),
        )
        Shipment.objects.create(order=order, external_id="shipment-unassigned")
        self.login()

        filtered = self.client.get("/api/pedidos/?assignment=unassigned")
        overview = self.client.get("/api/pedidos/overview/")

        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["orders"][0]["channel_order_id"], "19337")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["unassigned"], 1)

    def test_stale_version_does_not_write(self):
        self.login()
        response = self.client.patch(
            f"/api/pedidos/shipments/{self.shipment_a.id}/",
            data={"version": 999, "carrier": "No debe guardarse"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.carrier, "")

    def test_unsafe_tracking_url_is_rejected_without_partial_write(self):
        self.login()
        response = self.client.patch(
            f"/api/pedidos/shipments/{self.shipment_a.id}/",
            data={"version": 1, "carrier": "X", "tracking_url": "javascript:alert(1)"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.carrier, "")

    def test_whatsapp_prepares_one_message_per_active_contact_without_guide(self):
        config = MessagingConfig.objects.create(warehouse=self.location)
        MessagingContact.objects.create(
            config=config, name="Contacto 1", phone="573000000001", active=True
        )
        MessagingContact.objects.create(
            config=config, name="Contacto 2", phone="573000000002", active=True
        )
        MessagingContact.objects.create(
            config=config, name="Inactivo", phone="573000000003", active=False
        )
        self.login()
        response = self.client.post(
            "/api/pedidos/messaging/manual/",
            data={"shipment_ids": [str(self.shipment_a.id)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["recipientCount"], 2)
        self.assertEqual(payload["externalWrites"], 0)
        self.assertTrue(all("Pedido 19335" in item["rendered_message"] for item in payload["generated"]))
        self.assertTrue(all("SKU 8844 - Artículo Barú x 3" in item["rendered_message"] for item in payload["generated"]))
        self.assertTrue(all("Sin guía" in item["rendered_message"] for item in payload["generated"]))

    def test_whatsapp_separates_each_shipment_and_fans_out_to_contacts(self):
        self.shipment_b.warehouse = self.location
        self.shipment_b.warehouse_name = "Barú"
        self.shipment_b.save(update_fields=["warehouse", "warehouse_name"])
        config = MessagingConfig.objects.create(warehouse=self.location)
        for index in range(2):
            MessagingContact.objects.create(
                config=config,
                name=f"Contacto {index + 1}",
                phone=f"57300000001{index}",
            )
        self.login()
        response = self.client.post(
            "/api/pedidos/messaging/manual/",
            {"shipment_ids": [str(self.shipment_a.id), str(self.shipment_b.id)]},
            content_type="application/json",
        )
        payload = response.json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["recipientCount"], 4)
        self.assertEqual(payload["shipmentCount"], 2)
        self.assertEqual({item["shipmentId"] for item in payload["generated"]}, {
            str(self.shipment_a.id), str(self.shipment_b.id)
        })
        for item in payload["generated"]:
            if item["shipmentId"] == str(self.shipment_a.id):
                self.assertIn("8844", item["rendered_message"])
                self.assertNotIn("GV-L025", item["rendered_message"])
            else:
                self.assertIn("GV-L025", item["rendered_message"])
                self.assertNotIn("8844", item["rendered_message"])

    def test_whatsapp_disabled_warehouse_is_explicitly_skipped(self):
        MessagingConfig.objects.create(warehouse=self.location, active=False)
        self.login()
        response = self.client.post(
            "/api/pedidos/messaging/manual/",
            {"shipment_ids": [str(self.shipment_a.id)]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["skipped"][0]["reason"],
            "warehouse_messaging_disabled",
        )
        self.assertEqual(ManualFollowup.objects.count(), 0)

    def test_whatsapp_prepare_is_replayable_without_duplicate_rows(self):
        config = MessagingConfig.objects.create(warehouse=self.location)
        MessagingContact.objects.create(
            config=config, name="Contacto", phone="573000000010"
        )
        self.login()
        data = {"shipment_ids": [str(self.shipment_a.id)]}
        first = self.client.post(
            "/api/pedidos/messaging/manual/", data, content_type="application/json"
        )
        second = self.client.post(
            "/api/pedidos/messaging/manual/", data, content_type="application/json"
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(ManualFollowup.objects.count(), 1)
        self.assertEqual(first.json()["generated"][0]["id"], second.json()["generated"][0]["id"])
        self.assertTrue(second.json()["generated"][0]["replayed"])

    def test_contact_book_rejects_duplicate_phone_without_losing_existing_contacts(self):
        config = MessagingConfig.objects.create(warehouse=self.location)
        first = MessagingContact.objects.create(
            config=config, name="Primero", phone="573000000021"
        )
        second = MessagingContact.objects.create(
            config=config, name="Segundo", phone="573000000022"
        )
        self.login()
        response = self.client.put(
            "/api/pedidos/messaging/configs/",
            {
                "warehouse_id": self.location.id,
                "active": True,
                "contacts": [
                    {"id": first.id, "name": "Primero editado", "phone": "573000000021", "active": True},
                    {"id": second.id, "name": "Segundo", "phone": "573000000021", "active": True},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            list(config.contacts.order_by("id").values_list("name", "phone")),
            [("Primero", "573000000021"), ("Segundo", "573000000022")],
        )

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_private_manual_guide_upload_and_authenticated_read(self):
        self.login()
        file = SimpleUploadedFile(
            "guia-19335.pdf", b"%PDF-1.4\nlocal qa", content_type="application/pdf"
        )
        response = self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/document/",
            data={"file": file},
        )
        self.assertEqual(response.status_code, 201)
        self.client.logout()
        protected = self.client.get(
            f"/api/pedidos/shipments/{self.shipment_a.id}/document/"
        )
        self.assertEqual(protected.status_code, 403)

    def test_external_sync_endpoint_is_a_hard_shield(self):
        admin = User.objects.create_superuser(
            username="admin.orders@pamo.test", email="admin.orders@pamo.test"
        )
        self.client.force_login(admin)
        response = self.client.post("/api/pedidos/sync/shopify/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["externalWrites"], 0)

    def configure_supplier(self, phone="573000004936"):
        config = MessagingConfig.objects.create(warehouse=self.location)
        return MessagingContact.objects.create(
            config=config, name="Proveedor QA", phone=phone, active=True
        )

    def test_supplier_received_response_is_idempotent(self):
        self.configure_supplier()
        first = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="order_received",
            provider_event_id="wamid.received-1",
            sender_phone="+57 300 000 4936",
        )
        replay = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="order_received",
            provider_event_id="wamid.received-1",
            sender_phone="+57 300 000 4936",
        )
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.supplier_state, "received")
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(SupplierResponseEvent.objects.count(), 1)

    def test_supplier_requests_guide_before_pdf_and_upload_makes_it_ready(self):
        self.configure_supplier()
        result = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="request_guide",
            provider_event_id="wamid.guide-1",
            sender_phone="573000004936",
        )
        self.assertEqual(result["guideDeliveryState"], "requested")

        self.login()
        file = SimpleUploadedFile(
            "guia-19335.pdf", b"%PDF-1.4\nlocal qa", content_type="application/pdf"
        )
        response = self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/document/", {"file": file}
        )
        self.assertEqual(response.status_code, 201)
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.guide_delivery_state, "ready_to_send")

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_existing_guide_is_ready_to_send_when_supplier_requests_it(self):
        self.configure_supplier()
        self.login()
        file = SimpleUploadedFile(
            "guia-lista.pdf", b"%PDF-1.4\nlocal qa", content_type="application/pdf"
        )
        self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/document/", {"file": file}
        )
        result = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="request_guide",
            provider_event_id="wamid.guide-2",
            sender_phone="573000004936",
        )
        self.assertEqual(result["guideDeliveryState"], "ready_to_send")

    def test_supplier_novelty_is_separate_from_carrier_exception(self):
        self.configure_supplier()
        apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="report_issue",
            provider_event_id="wamid.issue-1",
            sender_phone="573000004936",
        )
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.supplier_state, "issue_reported")
        self.assertEqual(self.shipment_a.logistics_state, "without_guide")
        self.assertEqual(ShipmentNovelty.objects.filter(state="open").count(), 1)

        result = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="request_guide",
            provider_event_id="wamid.guide-blocked",
            sender_phone="573000004936",
        )
        self.assertEqual(result["result"], "review")
        self.assertEqual(result["guideDeliveryState"], "not_requested")

    def test_supplier_novelty_category_updates_exact_open_novelty_and_is_idempotent(self):
        self.configure_supplier()
        opened = apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="report_issue",
            provider_event_id="wamid.issue-category-open",
            sender_phone="573000004936",
        )
        self.assertEqual(opened["openNovelty"]["detailState"], "awaiting_category")
        first = apply_supplier_novelty_category(
            shipment_id=self.shipment_a.id,
            category="supplier_stockout",
            provider_event_id="wamid.issue-category-1",
            sender_phone="573000004936",
        )
        replay = apply_supplier_novelty_category(
            shipment_id=self.shipment_a.id,
            category="supplier_stockout",
            provider_event_id="wamid.issue-category-1",
            sender_phone="573000004936",
        )
        novelty = ShipmentNovelty.objects.get(shipment=self.shipment_a, state="open")
        self.assertEqual(novelty.category, "supplier_stockout")
        self.assertEqual(novelty.detail_state, "awaiting_item")
        self.assertIn("Selecciona el SKU agotado", first["nextPrompt"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            SupplierResponseEvent.objects.filter(action="classify_issue").count(), 1
        )
        with self.assertRaises(SupplierResponseError) as caught:
            apply_supplier_novelty_category(
                shipment_id=self.shipment_a.id,
                category="supplier_delay",
                provider_event_id="wamid.issue-category-overwrite",
                sender_phone="573000004936",
            )
        self.assertEqual(
            caught.exception.code, "supplier_novelty_category_already_recorded"
        )

    def test_supplier_novelty_category_rejects_wrong_warehouse_contact(self):
        self.configure_supplier("573001112233")
        apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="report_issue",
            provider_event_id="wamid.issue-category-open-2",
            sender_phone="573001112233",
        )
        with self.assertRaises(SupplierResponseError) as caught:
            apply_supplier_novelty_category(
                shipment_id=self.shipment_a.id,
                category="supplier_delay",
                provider_event_id="wamid.issue-category-wrong",
                sender_phone="573000004936",
            )
        self.assertEqual(caught.exception.code, "supplier_contact_mismatch")

    def test_supplier_novelty_detail_completes_the_exact_open_novelty(self):
        self.configure_supplier()
        apply_supplier_response(
            shipment_id=self.shipment_a.id,
            action="report_issue",
            provider_event_id="wamid.detail-open",
            sender_phone="573000004936",
        )
        apply_supplier_novelty_category(
            shipment_id=self.shipment_a.id,
            category="supplier_other",
            provider_event_id="wamid.detail-category",
            sender_phone="573000004936",
        )
        result = apply_supplier_novelty_detail(
            shipment_id=self.shipment_a.id,
            detail="  SKU 8844: solo hay 2 unidades.  ",
            provider_event_id="wamid.detail-text",
            sender_phone="573000004936",
        )
        novelty = ShipmentNovelty.objects.get(shipment=self.shipment_a)
        self.assertEqual(novelty.detail, "SKU 8844: solo hay 2 unidades.")
        self.assertEqual(novelty.detail_state, "complete")
        self.assertEqual(result["openNovelty"]["detailState"], "complete")

    @override_settings(DEBUG=True)
    def test_local_simulator_classifies_open_novelty_without_external_write(self):
        self.login()
        self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/supplier-response/simulate/",
            {"action": "report_issue", "event_id": "local-ui-issue-open"},
            content_type="application/json",
        )
        response = self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/supplier-response/simulate/",
            {
                "action": "classify_issue",
                "category": "supplier_partial",
                "event_id": "local-ui-issue-category",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["externalWrites"], 0)
        novelty = ShipmentNovelty.objects.get(shipment=self.shipment_a)
        self.assertEqual(novelty.category, "supplier_partial")
        self.assertEqual(novelty.detail_state, "awaiting_item")

    def test_response_from_wrong_supplier_is_rejected(self):
        self.configure_supplier("573001112233")
        with self.assertRaises(SupplierResponseError) as caught:
            apply_supplier_response(
                shipment_id=self.shipment_a.id,
                action="order_received",
                provider_event_id="wamid.wrong-contact",
                sender_phone="573000004936",
            )
        self.assertEqual(caught.exception.code, "supplier_contact_mismatch")

    @override_settings(DEBUG=True)
    def test_local_simulator_updates_supplier_state_without_external_write(self):
        self.login()
        response = self.client.post(
            f"/api/pedidos/shipments/{self.shipment_a.id}/supplier-response/simulate/",
            {"action": "order_received", "event_id": "local-ui-test-0001"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["externalWrites"], 0)
        self.shipment_a.refresh_from_db()
        self.assertEqual(self.shipment_a.supplier_state, "received")
