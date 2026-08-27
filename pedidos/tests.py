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
    MessagingConfig,
    MessagingContact,
    Order,
    OrderItem,
    Shipment,
    ShipmentItem,
    WarehouseLocation,
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
        self.assertEqual(payload["externalWrites"], 0)

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

    def test_pdf_pending_filter_uses_canonical_document_metadata(self):
        self.shipment_a.tracking_number = "GUIA-A"
        self.shipment_b.tracking_number = "GUIA-B"
        self.shipment_a.save()
        self.shipment_b.save()
        self.login()

        pending = self.client.get("/api/pedidos/?guide=pdf_missing").json()
        self.assertEqual(pending["total"], 1)

        for shipment in (self.shipment_a, self.shipment_b):
            shipment.source_snapshot = {
                "canonical_shipment_id": str(shipment.id),
                "remote_documents": [{"source": "envia", "original_filename": "guia.pdf"}],
            }
            shipment.save()

        complete = self.client.get("/api/pedidos/?guide=pdf_missing").json()
        regular = self.client.get("/api/pedidos/").json()
        overview = self.client.get("/api/pedidos/overview/").json()
        self.assertEqual(complete["total"], 0)
        self.assertEqual(regular["orders"][0]["pdf_available"], [True, True])
        self.assertEqual(overview["without_pdf"], 0)

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
        self.assertTrue(all("SKU 8844 × 3" in item["rendered_message"] for item in payload["generated"]))
        self.assertTrue(all("Sin guía" in item["rendered_message"] for item in payload["generated"]))

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
