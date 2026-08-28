from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from integrations.orders.canonical import PamoCanonicalOrdersProvider
from pedidos.functions.canonical_import import apply_canonical_snapshot
from pedidos.functions.querysets import operational_orders
from pedidos.models import Order, Shipment, WarehouseLocation


class FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class CanonicalProviderTests(TestCase):
    def test_provider_uses_authenticated_get_without_redirects(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse({"orders": [], "items": [], "total": 0})

        provider = PamoCanonicalOrdersProvider(
            base_url="https://api.example.test",
            api_token="secret-not-logged",
            enabled=True,
            request_callable=fake_get,
        )
        provider.export_orders(from_date="2026-06-27", to_date="2026-08-27")
        self.assertEqual(calls[0][0], "https://api.example.test/v1/orders/logistics/export")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer secret-not-logged")
        self.assertFalse(calls[0][1]["allow_redirects"])


class CanonicalImportTests(TestCase):
    def setUp(self):
        self.order_id = str(uuid4())
        self.item_id = str(uuid4())
        self.shipment_id = str(uuid4())
        self.event_id = str(uuid4())
        self.now = timezone.now()

    def snapshot(self):
        detail = {
            "id": self.order_id,
            "channel": "shopify",
            "external_id": "shopify-order-1",
            "channel_order_id": "20001",
            "placed_at": self.now.isoformat(),
            "customer_name": "Cliente real",
            "customer_email": "cliente@example.test",
            "currency": "COP",
            "grand_total": "120000",
            "state": "open",
            "items": [
                {
                    "id": self.item_id,
                    "external_id": "line-1",
                    "sku": "SKU-1",
                    "name": "Producto",
                    "quantity": 2,
                    "unit_price": "60000",
                    "line_total": "120000",
                }
            ],
            "shipments": [
                {
                    "id": self.shipment_id,
                    "external_id": "shipment-1",
                    "warehouse_location_id": "remote-location",
                    "warehouse_name": "Bodega remota",
                    "warehouse_assignment_source": "integration",
                    "carrier": "Transportadora",
                    "tracking_number": "REMOTE-GUIDE",
                    "tracking_source": "integration",
                    "logistics_state": "in_transit",
                    "version": 2,
                    "items": [{"order_item_id": self.item_id, "quantity": 2}],
                    "tracking_events": [
                        {
                            "id": self.event_id,
                            "external_event_id": "event-1",
                            "source": "envia",
                            "state_normalized": "in_transit",
                            "state_original": "En tránsito",
                            "occurred_at": (self.now - timedelta(hours=1)).isoformat(),
                        }
                    ],
                    "documents": [],
                }
            ],
        }
        return {
            "orders": [
                {
                    "id": self.order_id,
                    "channel": "shopify",
                    "external_id": "shopify-order-1",
                    "channel_order_id": "20001",
                    "channel_order_url": "https://admin.shopify.com/order/1",
                }
            ],
            "items": [],
            "total": 1,
        }, {self.order_id: detail}

    def test_import_is_idempotent_and_preserves_manual_assignments(self):
        manual_warehouse = WarehouseLocation.objects.create(
            external_id="manual-location",
            name="Proveedor manual",
        )
        existing_order = Order.objects.create(
            channel="shopify",
            external_id="shopify-order-1",
            visible_id="old",
            placed_at=self.now,
        )
        Shipment.objects.create(
            order=existing_order,
            external_id="shipment-1",
            warehouse=manual_warehouse,
            warehouse_name=manual_warehouse.name,
            warehouse_locked=True,
            warehouse_assignment_source="manual",
            tracking_number="MANUAL-GUIDE",
            tracking_source="manual",
        )
        payload, details = self.snapshot()
        apply_canonical_snapshot(
            export_payload=payload,
            details=details,
            from_date="2026-06-27",
            to_date="2026-08-27",
        )
        apply_canonical_snapshot(
            export_payload=payload,
            details=details,
            from_date="2026-06-27",
            to_date="2026-08-27",
        )
        shipment = Shipment.objects.get(order=existing_order, external_id="shipment-1")
        self.assertEqual(shipment.warehouse_id, manual_warehouse.id)
        self.assertEqual(shipment.tracking_number, "MANUAL-GUIDE")
        self.assertEqual(existing_order.items.count(), 1)
        self.assertEqual(existing_order.shipments.count(), 1)
        self.assertEqual(shipment.tracking_events.count(), 1)

    def test_fixtures_are_preserved_but_hidden_when_real_orders_exist(self):
        Order.objects.create(
            channel="shopify",
            external_id="fixture",
            visible_id="fixture",
            placed_at=self.now,
            source_snapshot={"localFixture": True, "sanitized": True},
        )
        self.assertEqual(operational_orders().count(), 1)
        Order.objects.create(
            channel="shopify",
            external_id="real",
            visible_id="real",
            placed_at=self.now,
            source_snapshot={"canonicalImport": True},
        )
        self.assertEqual(operational_orders().count(), 1)
        self.assertEqual(operational_orders().first().external_id, "real")

    def test_shopify_sodimac_marker_sets_business_origin_without_changing_source(self):
        payload, details = self.snapshot()
        detail = details[self.order_id]
        detail["customer_email"] = "recepcion.sodimac@example.test"
        detail["source_snapshot"] = {"email": "recepcion.sodimac@example.test"}

        apply_canonical_snapshot(
            export_payload=payload,
            details=details,
            from_date="2026-08-08",
            to_date="2026-08-27",
        )

        order = Order.objects.get(external_id="shopify-order-1")
        self.assertEqual(order.channel, "shopify")
        self.assertEqual(order.source_snapshot["business_origin"], "sodimac")
        self.assertEqual(order.source_snapshot["business_origin_via"], "shopify")
        self.assertEqual(
            order.source_snapshot["business_origin_confidence"],
            "explicit_source_email_marker",
        )

    def test_mercado_libre_fulfillment_is_marked_without_seller_pdf(self):
        payload, details = self.snapshot()
        payload["orders"][0]["channel"] = "mercado-libre"
        payload["orders"][0]["external_id"] = "2000018144943652"
        payload["orders"][0]["channel_order_id"] = "2000018144943652"
        detail = details[self.order_id]
        detail["channel"] = "mercado-libre"
        detail["external_id"] = "2000018144943652"
        detail["channel_order_id"] = "2000018144943652"
        detail["shipments"][0]["external_id"] = "meli:47868842483"
        detail["shipments"][0]["source_snapshot"] = {
            "status": "ready_to_ship",
            "substatus": "in_packing_list",
            "logistic": {"mode": "me2", "type": "fulfillment"},
        }

        apply_canonical_snapshot(
            export_payload=payload,
            details=details,
            from_date="2026-08-27",
            to_date="2026-08-28",
        )

        shipment = Shipment.objects.get(external_id="meli:47868842483")
        self.assertEqual(
            shipment.source_snapshot["label_availability"]["status"],
            "not_printable",
        )
        self.assertEqual(
            shipment.source_snapshot["label_availability"]["reason"],
            "MERCADOLIBRE_FULFILLMENT_NO_SELLER_LABEL",
        )
        self.assertEqual(
            shipment.source_snapshot["label_source"]["logistic_type"],
            "fulfillment",
        )
