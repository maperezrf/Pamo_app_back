import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from catalogo.models import (
    IntegrationReadStatus,
    InventoryLevel,
    LogisticsQuoteSnapshot,
    MasterProduct,
    ProductVariant,
)

from .models import LogisticsAudit, Order, OrderItem, Shipment, ShipmentItem, WarehouseLocation


User = get_user_model()


class ShipmentShippingPlanTests(TestCase):
    def setUp(self):
        group = Group.objects.create(name="Operaciones")
        self.user = User.objects.create_user(
            username="qa.shipping@pamo.test",
            email="qa.shipping@pamo.test",
        )
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.warehouse = WarehouseLocation.objects.create(
            external_id="shopify-origin-qa",
            name="Barú",
            reference="BARU",
        )
        self.order = Order.objects.create(
            channel="shopify",
            external_id="shipping-order-qa",
            visible_id="19596",
            placed_at=timezone.now(),
            customer_name="Cliente QA",
            customer_phone="573000000000",
            grand_total=Decimal("180000"),
        )
        item = OrderItem.objects.create(
            order=self.order,
            external_id="shipping-item-qa",
            sku="SKU-SHIPPING-QA",
            name="Artículo para envío",
            quantity=1,
            unit_price=Decimal("180000"),
            line_total=Decimal("180000"),
        )
        self.shipment = Shipment.objects.create(
            order=self.order,
            external_id="shipping-shipment-qa",
            warehouse=self.warehouse,
            warehouse_name=self.warehouse.name,
        )
        ShipmentItem.objects.create(shipment=self.shipment, order_item=item, quantity=1)
        product = MasterProduct.objects.create(
            shopify_product_id="shipping-product-qa",
            title="Artículo para envío",
            status="ACTIVE",
        )
        self.variant = ProductVariant.objects.create(
            product=product,
            sku=item.sku,
            shopify_variant_id="shipping-variant-qa",
            price=Decimal("180000"),
        )

    @property
    def endpoint(self):
        return f"/api/pedidos/shipments/{self.shipment.id}/shipping-plan/"

    def test_plan_fails_closed_until_origin_destination_package_and_quote_exist(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["readyToPrepare"])
        self.assertEqual(payload["externalWrites"], 0)
        self.assertTrue(any("dirección" in item for item in payload["blockers"]))
        self.assertTrue(any("peso" in item for item in payload["blockers"]))

    def test_human_can_select_current_quote_and_prepare_without_external_write(self):
        InventoryLevel.objects.create(
            variant=self.variant,
            location_external_id=self.warehouse.external_id,
            location_name=self.warehouse.name,
            available=Decimal("10"),
            observed_at=timezone.now(),
            origin_address={
                "address1": "Origen QA",
                "city": "Bogotá",
                "countryCode": "CO",
            },
            address_verified=True,
            fulfills_online_orders=True,
        )
        LogisticsQuoteSnapshot.objects.create(
            variant=self.variant,
            provider="ENVIA",
            basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
            status=IntegrationReadStatus.Status.AVAILABLE,
            destination={"city": "11001000", "department": "Bogotá D.C."},
            weight_kg=Decimal("2"),
            dimensions={"length_cm": 30, "width_cm": 20, "height_cm": 15},
            carrier="Coordinadora",
            delivery_estimate="2 días",
            amount=Decimal("16500"),
            currency="COP",
            observed_at=timezone.now(),
            fingerprint="a" * 64,
            external_writes=0,
        )
        saved = self.client.patch(
            self.endpoint,
            data={
                "version": self.shipment.version,
                "destination": {
                    "address": "Calle Privada 1",
                    "city": "Bogotá",
                    "department": "Bogotá D.C.",
                    "dane_code": "11001000",
                    "country": "CO",
                },
                "package": {
                    "weight_kg": "2",
                    "length_cm": "30",
                    "width_cm": "20",
                    "height_cm": "15",
                    "confirmed": True,
                },
            },
            content_type="application/json",
        )

        self.assertEqual(saved.status_code, 200)
        plan = saved.json()["plan"]
        self.assertEqual(len(plan["quoteOptions"]), 1)
        self.assertFalse(plan["readyToPrepare"])
        fingerprint = plan["quoteOptions"][0]["fingerprint"]
        selected = self.client.patch(
            self.endpoint,
            data={
                "version": saved.json()["version"],
                "quote_fingerprint": fingerprint,
            },
            content_type="application/json",
        )

        self.assertEqual(selected.status_code, 200)
        self.assertTrue(selected.json()["plan"]["readyToPrepare"])
        prepared = self.client.post(
            self.endpoint,
            data={"action": "prepare"},
            content_type="application/json",
        )
        self.assertEqual(prepared.status_code, 200)
        self.assertTrue(prepared.json()["prepared"])
        self.assertEqual(prepared.json()["externalWrites"], 0)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.guide_request_state, "prepared")
        audit_text = json.dumps(
            list(LogisticsAudit.objects.values_list("detail", flat=True))
        )
        self.assertNotIn("Calle Privada 1", audit_text)

    def test_quote_replay_does_not_accept_stale_fingerprint(self):
        response = self.client.patch(
            self.endpoint,
            data={"version": self.shipment.version, "quote_fingerprint": "stale"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.shipping_quote_selection, {})
