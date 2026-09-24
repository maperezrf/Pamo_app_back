from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from integrations.shopify.functions.create_order import ShopifyOrderCreationError

from .functions.fetch_falabella_orders import fetch_falabella_orders
from .functions.import_falabella_orders import import_falabella_orders
from .functions.process_pending_orders import process_pending_orders
from .functions.select_fulfillment_location import select_fulfillment_location
from .models import MarketplaceOrder, MarketplaceOrderItem


def _raw_falabella_order(order_id="1", order_number="N-1", **overrides):
    data = {
        "order_id": order_id,
        "order_number": order_number,
        "created_at": "",
        "updated_at": "",
        "status": "pending",
        "status_raw": "pending",
        "total": "0",
        "customer_first_name": "Ana",
        "customer_last_name": "Gomez",
        "customer_identification": "123",
        "customer_email": "ana@example.com",
        "customer_address": "Calle 10 # 20-30, Apto 101",
        "customer_city": "MEDELLIN",
        "customer_region": "ANTIOQUIA",
        "customer_phone": "3001234567",
    }
    data.update(overrides)
    return data


class FetchFalabellaOrdersTests(TestCase):
    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_creates_marketplace_order_and_items_for_a_new_order(self, mock_get_orders, mock_get_items):
        mock_get_orders.return_value = [_raw_falabella_order()]
        mock_get_items.return_value = [
            {"sku": "SKU-1", "shop_sku": "S1", "quantity": 2, "price": "100.00", "status": "pending"}
        ]

        new_count = fetch_falabella_orders()

        self.assertEqual(new_count, 1)
        order = MarketplaceOrder.objects.get(marketplace_order_id="1")
        self.assertEqual(order.customer_identification, "123")
        self.assertEqual(order.customer_address, "Calle 10 # 20-30, Apto 101")
        self.assertEqual(order.customer_city, "MEDELLIN")
        self.assertEqual(order.customer_region, "ANTIOQUIA")
        self.assertEqual(order.customer_phone, "3001234567")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().marketplace_sku, "SKU-1")
        self.assertEqual(order.items.first().quantity, 2)

    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_does_not_duplicate_an_order_that_already_exists_locally(self, mock_get_orders, mock_get_items):
        mock_get_orders.return_value = [_raw_falabella_order()]
        mock_get_items.return_value = []
        fetch_falabella_orders()
        mock_get_items.reset_mock()

        new_count = fetch_falabella_orders()

        self.assertEqual(new_count, 0)
        self.assertEqual(MarketplaceOrder.objects.count(), 1)
        mock_get_items.assert_not_called()

    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_reports_progress_up_to_100(self, mock_get_orders, mock_get_items):
        mock_get_orders.return_value = [_raw_falabella_order()]
        mock_get_items.return_value = []
        calls = []

        fetch_falabella_orders(progress_callback=lambda percent, step=None: calls.append(percent))

        self.assertEqual(calls[-1], 100)

    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_default_created_after_is_now_minus_the_lookback(self, mock_get_orders, mock_get_items):
        mock_get_orders.return_value = []

        fetch_falabella_orders()

        _, kwargs = mock_get_orders.call_args
        self.assertAlmostEqual(
            (kwargs["created_before"] - kwargs["created_after"]).total_seconds(),
            48 * 3600,
            delta=5,
        )

    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_accepts_an_iso_string_for_created_after(self, mock_get_orders, mock_get_items):
        mock_get_orders.return_value = []

        fetch_falabella_orders(created_after="2026-09-22T00:00:00+00:00")

        _, kwargs = mock_get_orders.call_args
        self.assertEqual(kwargs["created_after"].isoformat(), "2026-09-22T00:00:00+00:00")

    @patch("orders.functions.fetch_falabella_orders.get_order_items")
    @patch("orders.functions.fetch_falabella_orders.get_orders")
    def test_accepts_a_datetime_for_created_after(self, mock_get_orders, mock_get_items):
        from datetime import datetime, timezone

        given = datetime(2026, 9, 22, tzinfo=timezone.utc)
        mock_get_orders.return_value = []

        fetch_falabella_orders(created_after=given)

        _, kwargs = mock_get_orders.call_args
        self.assertEqual(kwargs["created_after"], given)


FIXED_CUSTOMER_PATCH = "orders.functions.process_pending_orders.FALABELLA_SHOPIFY_CUSTOMER_ID"
PRIORITY_LOCATION_PATCH = "orders.functions.process_pending_orders.FULFILLMENT_PRIORITY_LOCATION_ID"
ENVIA = {"location_id": "97615380757", "name": "Bodega Envia", "available": 25}
PROVEEDORES = {"location_id": "94018535701", "name": "Proveedores", "available": 45}


def _inventory(variant_id="555", sku="SKU-1", tracked=True, locations=None):
    return {
        "variant_id": variant_id,
        "sku": sku,
        "tracked": tracked,
        "locations": [ENVIA, PROVEEDORES] if locations is None else locations,
    }


@patch(PRIORITY_LOCATION_PATCH, "97615380757")
@patch(FIXED_CUSTOMER_PATCH, "999")
class ProcessPendingOrdersTests(TestCase):
    def _make_order(self, **overrides):
        defaults = dict(
            marketplace=MarketplaceOrder.Marketplace.FALABELLA,
            marketplace_order_id="1",
            marketplace_order_number="N-1",
            customer_identification="123",
            customer_first_name="Ana",
            customer_last_name="Gomez",
            customer_email="ana@example.com",
        )
        defaults.update(overrides)
        order = MarketplaceOrder.objects.create(**defaults)
        MarketplaceOrderItem.objects.create(order=order, marketplace_sku="SKU-1", quantity=2, unit_price="100.00")
        return order

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_creates_the_order_for_the_fixed_customer(self, mock_variant, mock_create_order):
        order = self._make_order()
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.shopify_order_id, "777")
        self.assertEqual(order.shopify_order_name, "#1001")
        self.assertEqual(order.shopify_customer_id, "999")
        _, kwargs = mock_create_order.call_args
        self.assertEqual(kwargs["customer_id"], "999")
        self.assertEqual(kwargs["items"], [{"variant_id": "555", "quantity": 2, "price": "100.00"}])
        self.assertEqual(kwargs["tags"], ["falabella"])
        self.assertEqual(kwargs["note"], "Falabella #N-1 — Ana Gomez CC 123")

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_note_omits_buyer_parts_that_are_missing(self, mock_variant, mock_create_order):
        self._make_order(customer_first_name="", customer_last_name="", customer_identification="")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        _, kwargs = mock_create_order.call_args
        self.assertEqual(kwargs["note"], "Falabella #N-1")

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_fails_before_touching_orders_when_fixed_customer_is_not_configured(
        self, mock_variant, mock_create_order
    ):
        order = self._make_order()

        with patch(FIXED_CUSTOMER_PATCH, ""), self.assertRaises(ValueError):
            process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.PENDING)
        mock_variant.assert_not_called()
        mock_create_order.assert_not_called()

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_retries_an_order_left_in_the_obsolete_error_creando_cliente_state(
        self, mock_variant, mock_create_order
    ):
        order = self._make_order(status=MarketplaceOrder.Status.ERROR_CUSTOMER, error_description="sin email")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.error_description, "")

    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_marks_error_creando_orden_when_a_sku_does_not_resolve(self, mock_variant):
        order = self._make_order()
        mock_variant.return_value = None

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)
        self.assertIn("SKU-1", order.error_description)

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_marks_error_creando_orden_when_shopify_rejects_the_order(self, mock_variant, mock_create_order):
        order = self._make_order()
        mock_variant.return_value = _inventory()
        mock_create_order.side_effect = ShopifyOrderCreationError(["boom"])

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)

    @patch("orders.functions.process_pending_orders.create_order")
    def test_does_not_reprocess_an_order_that_already_has_a_shopify_order_id(self, mock_create_order):
        self._make_order(shopify_order_id="already-created")

        process_pending_orders()

        mock_create_order.assert_not_called()

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_limit_caps_how_many_pending_orders_are_processed(self, mock_variant, mock_create_order):
        self._make_order(marketplace_order_id="1")
        self._make_order(marketplace_order_id="2")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders(limit=1)

        self.assertEqual(
            MarketplaceOrder.objects.filter(status=MarketplaceOrder.Status.CREATED).count(), 1
        )
        self.assertEqual(
            MarketplaceOrder.objects.filter(status=MarketplaceOrder.Status.PENDING).count(), 1
        )

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_assigns_the_priority_location_and_stores_the_inventory_snapshot(self, mock_variant, mock_create_order):
        order = self._make_order()
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, MarketplaceOrder.FulfillmentStatus.ASSIGNED)
        self.assertEqual(order.fulfillment_location_id, "97615380757")
        self.assertEqual(order.fulfillment_location_name, "Bodega Envia")
        self.assertEqual(order.fulfillment_note, "")
        self.assertEqual(order.items.get().inventory_snapshot, [ENVIA, PROVEEDORES])

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_marks_novedad_and_still_creates_the_order_when_no_location_covers_it(
        self, mock_variant, mock_create_order
    ):
        order = self._make_order()
        mock_variant.return_value = _inventory(locations=[{**ENVIA, "available": 1}])
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.fulfillment_status, MarketplaceOrder.FulfillmentStatus.NOVEDAD)
        self.assertEqual(order.fulfillment_location_id, "")
        self.assertIn("SKU-1 x2", order.fulfillment_note)
        mock_create_order.assert_called_once()

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_queries_inventory_even_when_the_variant_id_is_cached(self, mock_variant, mock_create_order):
        order = self._make_order()
        order.items.update(shopify_variant_id="555")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        mock_variant.assert_called_once_with("SKU-1")

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_does_not_overwrite_a_manually_resolved_location(self, mock_variant, mock_create_order):
        order = self._make_order(
            fulfillment_status=MarketplaceOrder.FulfillmentStatus.RESOLVED,
            fulfillment_location_id="1",
            fulfillment_location_name="Baru",
            fulfillment_note="decidido por operaciones",
        )
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, MarketplaceOrder.FulfillmentStatus.RESOLVED)
        self.assertEqual(order.fulfillment_location_name, "Baru")
        self.assertEqual(order.fulfillment_note, "decidido por operaciones")

    @patch("orders.functions.process_pending_orders.get_variant_inventory_by_sku")
    def test_sku_error_leaves_the_location_unevaluated(self, mock_variant):
        order = self._make_order()
        mock_variant.return_value = None

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, MarketplaceOrder.FulfillmentStatus.PENDING)


class SelectFulfillmentLocationTests(SimpleTestCase):
    def _line(self, sku, quantity, locations, tracked=True):
        return {"sku": sku, "quantity": quantity, "tracked": tracked, "locations": locations}

    def _loc(self, location_id, name, available):
        return {"location_id": location_id, "name": name, "available": available}

    def test_prefers_the_priority_location_when_it_covers_the_whole_order(self):
        lines = [self._line("A", 2, [self._loc("E", "Bodega Envia", 2), self._loc("P", "Proveedores", 50)])]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result, {"location_id": "E", "name": "Bodega Envia", "reason": ""})

    def test_falls_back_to_another_location_that_covers_the_whole_order(self):
        lines = [
            self._line("A", 2, [self._loc("E", "Bodega Envia", 5), self._loc("P", "Proveedores", 5)]),
            self._line("B", 1, [self._loc("P", "Proveedores", 1)]),
        ]
        self.assertEqual(select_fulfillment_location(lines, "E")["location_id"], "P")

    def test_picks_the_location_with_most_units_among_those_that_cover(self):
        lines = [self._line("A", 1, [self._loc("B", "Baru", 3), self._loc("P", "Proveedores", 9)])]
        self.assertEqual(select_fulfillment_location(lines, "E")["location_id"], "P")

    def test_breaks_ties_alphabetically_by_name(self):
        lines = [self._line("A", 1, [self._loc("T", "Taumm", 4), self._loc("B", "Baru", 4)])]
        self.assertEqual(select_fulfillment_location(lines, "")["name"], "Baru")

    def test_is_novedad_when_each_item_is_in_a_different_location(self):
        lines = [
            self._line("A", 1, [self._loc("E", "Bodega Envia", 1)]),
            self._line("B", 1, [self._loc("P", "Proveedores", 1)]),
        ]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result["location_id"], "")
        self.assertEqual(
            result["reason"],
            "Ninguna bodega tiene el pedido completo: A x1 (Bodega Envia 1); B x1 (Proveedores 1)",
        )

    def test_is_novedad_when_nothing_has_stock(self):
        lines = [self._line("A", 1, [self._loc("E", "Bodega Envia", 0)])]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result["location_id"], "")
        self.assertIn("A x1 (sin stock)", result["reason"])

    def test_is_novedad_when_an_item_does_not_track_inventory(self):
        lines = [self._line("A", 1, [], tracked=False)]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result["location_id"], "")
        self.assertIn("A", result["reason"])

    def test_location_missing_for_an_item_counts_as_zero(self):
        lines = [
            self._line("A", 1, [self._loc("E", "Bodega Envia", 10)]),
            self._line("B", 1, [self._loc("P", "Proveedores", 10)]),
        ]
        self.assertEqual(select_fulfillment_location(lines, "E")["location_id"], "")


class ImportFalabellaOrdersTests(TestCase):
    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    def test_calls_fetch_then_process_and_reports_completion(self, mock_fetch, mock_process):
        manager = Mock()
        manager.attach_mock(mock_fetch, "fetch")
        manager.attach_mock(mock_process, "process")
        calls = []

        import_falabella_orders(progress_callback=lambda percent, step=None: calls.append(percent))

        self.assertEqual([name for name, _, _ in manager.mock_calls], ["fetch", "process"])
        self.assertEqual(calls[-1], 100)

    def test_no_longer_reconciles_the_customers_directory(self):
        from orders.functions import import_falabella_orders as module

        self.assertFalse(hasattr(module, "reconcile_from_shopify"))

    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    def test_forwards_limit_from_params_to_process_pending_orders(self, mock_fetch, mock_process):
        import_falabella_orders(params={"limit": 1})

        _, kwargs = mock_process.call_args
        self.assertEqual(kwargs["limit"], 1)

    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    def test_forwards_created_after_from_params_to_fetch_falabella_orders(self, mock_fetch, mock_process):
        import_falabella_orders(params={"created_after": "2026-09-22T00:00:00+00:00"})

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs["created_after"], "2026-09-22T00:00:00+00:00")


class ProcessRegistrationTests(TestCase):
    def test_process_type_seeded_by_migration(self):
        from orchestrator.models import ProcessType

        self.assertTrue(ProcessType.objects.filter(code="orders.import_falabella").exists())

    def test_process_registered_in_orchestrator_registry(self):
        from orchestrator.core.registry import is_registered

        self.assertTrue(is_registered("orders.import_falabella"))
