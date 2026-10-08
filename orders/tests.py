from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from integrations.shopify.functions.create_order import ShopifyOrderCreationError

from .functions.fetch_falabella_orders import fetch_falabella_orders
from .functions.import_falabella_orders import import_falabella_orders
from .functions.process_pending_orders import process_pending_orders
from .functions.select_fulfillment_location import select_fulfillment_location
from .models import MarketplaceOrder, MarketplaceOrderItem, ShopifyOrder, ShopifyOrderSyncState


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
SHIPMENT_FIND_EXISTING = "orders.functions.process_shipment.list_orders_page"
NO_SHOPIFY_ORDERS = {"orders": [], "has_next_page": False, "end_cursor": None}


class NoExistingShopifyOrderMixin:
    """`process_shipment` busca en Shopify, por la etiqueta del pedido, si
    la orden ya existe antes de crearla. Por defecto, no existe;
    `self.mock_find_existing` permite cambiarlo."""

    def setUp(self):
        super().setUp()
        patcher = patch(SHIPMENT_FIND_EXISTING, return_value=NO_SHOPIFY_ORDERS)
        self.mock_find_existing = patcher.start()
        self.addCleanup(patcher.stop)
PRIORITY_LOCATION_PATCH = "orders.functions.process_shipment.FULFILLMENT_PRIORITY_LOCATION_ID"
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
class ProcessPendingOrdersTests(NoExistingShopifyOrderMixin, TestCase):
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

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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
        self.assertEqual(kwargs["tags"], ["falabella", "falabella-N-1"])
        self.mock_find_existing.assert_called_once_with(first=1, query='tag:"falabella-N-1"')
        self.assertEqual(kwargs["note"], "Falabella #N-1 — Ana Gomez CC 123")

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_note_omits_buyer_parts_that_are_missing(self, mock_variant, mock_create_order):
        self._make_order(customer_first_name="", customer_last_name="", customer_identification="")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        _, kwargs = mock_create_order.call_args
        self.assertEqual(kwargs["note"], "Falabella #N-1")

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_does_not_touch_orders_outside_the_retryable_states(self, mock_variant, mock_create_order):
        # `procesando` (otra corrida o una que murió a mitad) y el obsoleto
        # `error_creando_cliente` quedan para revisión manual.
        processing = self._make_order(marketplace_order_id="1", status=MarketplaceOrder.Status.PROCESSING)
        obsolete = self._make_order(
            marketplace_order_id="2", status=MarketplaceOrder.Status.ERROR_CUSTOMER, error_description="sin email"
        )

        process_pending_orders()

        mock_create_order.assert_not_called()
        processing.refresh_from_db()
        obsolete.refresh_from_db()
        self.assertEqual(processing.status, MarketplaceOrder.Status.PROCESSING)
        self.assertEqual(obsolete.status, MarketplaceOrder.Status.ERROR_CUSTOMER)

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_retries_an_order_left_in_error_creando_orden(self, mock_variant, mock_create_order):
        order = self._make_order(status=MarketplaceOrder.Status.ERROR_ORDER, error_description="SKU no encontrado")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.error_description, "")

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_network_failure_creating_the_order_leaves_it_processing(self, mock_variant, mock_create_order):
        # No se sabe si la orden quedó creada: no se reintenta a ciegas.
        order = self._make_order()
        mock_variant.return_value = _inventory()
        mock_create_order.side_effect = ConnectionError("timeout")

        with self.assertRaises(ConnectionError):
            process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.PROCESSING)
        self.assertEqual(order.shopify_order_id, "")

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_links_an_order_that_already_exists_in_shopify_instead_of_creating_another(
        self, mock_variant, mock_create_order
    ):
        # Otro proceso (u otra base) ya la creó con la etiqueta del pedido.
        order = self._make_order()
        self.mock_find_existing.return_value = {
            "orders": [{"id": "555000", "name": "#20360"}], "has_next_page": False, "end_cursor": None
        }

        process_pending_orders()

        mock_create_order.assert_not_called()
        mock_variant.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.shopify_order_id, "555000")
        self.assertEqual(order.shopify_order_name, "#20360")
        self.assertEqual(order.shopify_customer_id, "999")

    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_marks_error_creando_orden_when_a_sku_does_not_resolve(self, mock_variant):
        order = self._make_order()
        mock_variant.return_value = None

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)
        self.assertIn("SKU-1", order.error_description)

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_marks_error_creando_orden_when_shopify_rejects_the_order(self, mock_variant, mock_create_order):
        order = self._make_order()
        mock_variant.return_value = _inventory()
        mock_create_order.side_effect = ShopifyOrderCreationError(["boom"])

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)

    @patch("orders.functions.process_shipment.create_order")
    def test_does_not_reprocess_an_order_that_already_has_a_shopify_order_id(self, mock_create_order):
        self._make_order(shopify_order_id="already-created")

        process_pending_orders()

        mock_create_order.assert_not_called()

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
    def test_queries_inventory_even_when_the_variant_id_is_cached(self, mock_variant, mock_create_order):
        order = self._make_order()
        order.items.update(shopify_variant_id="555")
        mock_variant.return_value = _inventory()
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        mock_variant.assert_called_once_with("SKU-1")

    @patch("orders.functions.process_shipment.create_order")
    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    @patch("orders.functions.process_shipment.get_variant_inventory_by_sku")
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

    def test_same_sku_on_two_lines_is_summed(self):
        # Dos órdenes del mismo envío (pack de Mercado Libre) con el mismo
        # SKU x1: una bodega con 1 unidad no cubre el envío.
        locations = [self._loc("E", "Bodega Envia", 1), self._loc("P", "Proveedores", 2)]
        lines = [self._line("A", 1, locations), self._line("A", 1, locations)]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result["location_id"], "P")

    def test_same_sku_on_two_lines_without_enough_stock_is_novedad(self):
        locations = [self._loc("E", "Bodega Envia", 1)]
        lines = [self._line("A", 1, locations), self._line("A", 1, locations)]
        result = select_fulfillment_location(lines, "E")
        self.assertEqual(result["location_id"], "")
        self.assertIn("A x2", result["reason"])


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

    def test_mercadolibre_process_types_seeded_by_migration(self):
        from orchestrator.models import ProcessType

        notification = ProcessType.objects.get(code="orders.process_mercadolibre_notification")
        recover = ProcessType.objects.get(code="orders.recover_mercadolibre")
        self.assertTrue(notification.allow_concurrent)
        self.assertFalse(recover.allow_concurrent)


# ---------------------------------------------------------------- Mercado Libre

ML = "orders.functions.process_mercadolibre_order"
ML_CUSTOMER_PATCH = f"{ML}.MERCADOLIBRE_SHOPIFY_CUSTOMER_ID"
SHIPMENT_CREATE_ORDER = "orders.functions.process_shipment.create_order"
SHIPMENT_VARIANT = "orders.functions.process_shipment.get_variant_inventory_by_sku"


def _ml_item(sku="SKU-1", item_id="MCO1", quantity=1, price="150000.0"):
    return {"sku": sku, "item_id": item_id, "quantity": quantity, "price": price, "title": "", "variation_id": ""}


def _ml_order(order_id="2001", status="paid", pack_id="", shipment_id="S1", items=None):
    return {
        "order_id": order_id,
        "pack_id": pack_id,
        "shipment_id": shipment_id,
        "billing_info_id": f"B{order_id}",
        "status": status,
        "created_at": "",
        "buyer": {"id": "9", "nickname": "X", "first_name": "Ana", "last_name": "Pérez"},
        "items": [_ml_item()] if items is None else items,
    }


def _ml_billing(identification_type="CC", identification="100", first_name="Ana", last_name="Pérez", customer_type="CO"):
    return {
        "customer_identification_type": identification_type,
        "customer_identification": identification,
        "customer_first_name": first_name,
        "customer_last_name": last_name,
        "customer_address": "Calle 1 #2-3",
        "customer_city": "Bogotá",
        "customer_region": "Bogotá D.C.",
        "customer_type": customer_type,
    }


def _ml_shipment(logistic_type="cross_docking", items=None):
    return {
        "shipment_id": "S1",
        "status": "ready_to_ship",
        "logistic_type": logistic_type,
        "items": [{"item_id": "MCO1", "quantity": 1}] if items is None else items,
        "receiver": {},
    }


@patch(PRIORITY_LOCATION_PATCH.replace("process_pending_orders", "process_shipment"), "97615380757")
@patch(ML_CUSTOMER_PATCH, "888")
@patch(SHIPMENT_CREATE_ORDER, return_value={"order_id": "777", "order_name": "#1001"})
@patch(SHIPMENT_VARIANT, return_value=_inventory())
@patch(f"{ML}.get_billing_info", return_value=_ml_billing())
@patch(f"{ML}.get_pack")
@patch(f"{ML}.get_shipment", return_value=_ml_shipment())
@patch(f"{ML}.get_order", return_value=_ml_order())
class ProcessMercadoLibreOrderTests(NoExistingShopifyOrderMixin, TestCase):
    def _process(self, order_id="2001"):
        from .functions.process_mercadolibre_order import process_mercadolibre_order

        return process_mercadolibre_order(order_id)

    def test_paid_order_is_persisted_and_created_for_the_fixed_customer(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        outcome = self._process()

        order = MarketplaceOrder.objects.get(marketplace_order_id="2001")
        self.assertEqual(outcome, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.marketplace, MarketplaceOrder.Marketplace.MERCADOLIBRE)
        self.assertEqual(order.marketplace_order_number, "2001")
        self.assertEqual(order.shipment_id, "S1")
        self.assertEqual((order.customer_identification_type, order.customer_identification), ("CC", "100"))
        self.assertEqual(order.customer_type, "CO")
        self.assertEqual(order.customer_city, "Bogotá")
        self.assertEqual(order.shopify_order_id, "777")
        self.assertEqual(order.shopify_customer_id, "888")
        self.assertEqual(order.items.get().marketplace_sku, "SKU-1")
        _, kwargs = create_order.call_args
        self.assertEqual(kwargs["customer_id"], "888")
        self.assertEqual(kwargs["tags"], ["mercadolibre", "mercadolibre-2001"])
        self.assertIs(kwargs["requires_shipping"], False)
        self.assertEqual(kwargs["note"], "Mercado Libre #2001 — Ana Pérez CC 100")
        get_pack.assert_not_called()

    def test_business_buyer_note_uses_nit_and_company_name(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        get_billing.return_value = _ml_billing("NIT", "900123", first_name="Ferretería SAS", last_name="", customer_type="BU")

        self._process()

        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.customer_last_name, "")  # no mezcla el apellido del comprador
        self.assertEqual(create_order.call_args.kwargs["note"], "Mercado Libre #2001 — Ferretería SAS NIT 900123")

    def test_unpaid_order_leaves_nothing_behind(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import NOT_PAID

        get_order.return_value = _ml_order(status="payment_required")

        self.assertEqual(self._process(), NOT_PAID)
        self.assertFalse(MarketplaceOrder.objects.exists())
        create_order.assert_not_called()

    def test_full_order_is_skipped(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import FULFILLMENT

        get_shipment.return_value = _ml_shipment(logistic_type="fulfillment")

        self.assertEqual(self._process(), FULFILLMENT)
        self.assertFalse(MarketplaceOrder.objects.exists())
        create_order.assert_not_called()

    def test_second_notification_of_a_created_order_does_nothing(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import ALREADY_HANDLED

        self._process()
        get_order.reset_mock()

        self.assertEqual(self._process(), ALREADY_HANDLED)
        get_order.assert_not_called()
        create_order.assert_called_once()

    def test_order_in_processing_is_not_touched(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import ALREADY_HANDLED

        MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE,
            marketplace_order_id="2001",
            status=MarketplaceOrder.Status.PROCESSING,
        )

        self.assertEqual(self._process(), ALREADY_HANDLED)
        get_order.assert_not_called()

    def test_pack_is_one_shopify_order_with_one_location(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        orders = {
            "2001": _ml_order("2001", pack_id="P1", items=[_ml_item("SKU-1", "MCO1")]),
            "2002": _ml_order("2002", pack_id="P1", items=[_ml_item("SKU-2", "MCO2")]),
        }
        get_order.side_effect = lambda order_id: orders[order_id]
        get_pack.return_value = {"pack_id": "P1", "order_ids": ["2001", "2002"], "shipment_id": "S1", "status": "released"}
        get_shipment.return_value = _ml_shipment(items=[{"item_id": "MCO1", "quantity": 1}, {"item_id": "MCO2", "quantity": 1}])

        self._process("2001")

        create_order.assert_called_once()
        self.assertEqual(len(create_order.call_args.kwargs["items"]), 2)
        self.assertEqual(create_order.call_args.kwargs["note"], "Mercado Libre #P1 — Ana Pérez CC 100")
        # Un pack comparte número: una sola etiqueta de pedido.
        self.assertEqual(create_order.call_args.kwargs["tags"], ["mercadolibre", "mercadolibre-P1"])
        rows = MarketplaceOrder.objects.order_by("marketplace_order_id")
        self.assertEqual([row.shopify_order_id for row in rows], ["777", "777"])
        self.assertEqual({row.fulfillment_location_id for row in rows}, {"97615380757"})
        self.assertEqual({row.marketplace_order_number for row in rows}, {"P1"})

    def test_pack_without_a_location_for_the_whole_shipment_is_novedad_in_all_orders(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        orders = {
            "2001": _ml_order("2001", pack_id="P1", items=[_ml_item("SKU-1", "MCO1")]),
            "2002": _ml_order("2002", pack_id="P1", items=[_ml_item("SKU-2", "MCO2")]),
        }
        get_order.side_effect = lambda order_id: orders[order_id]
        get_pack.return_value = {"pack_id": "P1", "order_ids": ["2001", "2002"], "shipment_id": "S1", "status": "released"}
        get_shipment.return_value = _ml_shipment(items=[{"item_id": "MCO1", "quantity": 1}, {"item_id": "MCO2", "quantity": 1}])
        # cada SKU está en una bodega distinta: ninguna despacha el envío completo
        variant.side_effect = lambda sku: _inventory(
            sku=sku, locations=[ENVIA] if sku == "SKU-1" else [PROVEEDORES]
        )

        self._process("2001")

        rows = MarketplaceOrder.objects.all()
        self.assertEqual({row.fulfillment_status for row in rows}, {MarketplaceOrder.FulfillmentStatus.NOVEDAD})
        self.assertEqual({row.status for row in rows}, {MarketplaceOrder.Status.CREATED})
        create_order.assert_called_once()

    def test_pack_with_an_unpaid_order_waits(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import INCOMPLETE

        orders = {
            "2001": _ml_order("2001", pack_id="P1"),
            "2002": _ml_order("2002", pack_id="P1", status="payment_in_process"),
        }
        get_order.side_effect = lambda order_id: orders[order_id]
        get_pack.return_value = {"pack_id": "P1", "order_ids": ["2001", "2002"], "shipment_id": "S1", "status": "released"}

        self.assertEqual(self._process("2001"), INCOMPLETE)
        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.marketplace_order_id, "2001")
        self.assertEqual(order.status, MarketplaceOrder.Status.PENDING)
        self.assertIn("2002", order.error_description)
        create_order.assert_not_called()

    def test_items_not_matching_the_shipment_wait(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import INCOMPLETE

        get_shipment.return_value = _ml_shipment(items=[{"item_id": "MCO1", "quantity": 1}, {"item_id": "MCO9", "quantity": 1}])

        self.assertEqual(self._process(), INCOMPLETE)
        create_order.assert_not_called()

    def test_pack_order_claimed_by_another_process_is_left_alone(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from .functions.process_mercadolibre_order import CLAIMED_ELSEWHERE

        MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE,
            marketplace_order_id="2002",
            shipment_id="S1",
            status=MarketplaceOrder.Status.PROCESSING,
        )
        orders = {"2001": _ml_order("2001", pack_id="P1"), "2002": _ml_order("2002", pack_id="P1")}
        get_order.side_effect = lambda order_id: orders[order_id]
        get_pack.return_value = {"pack_id": "P1", "order_ids": ["2001", "2002"], "shipment_id": "S1", "status": "released"}
        get_shipment.return_value = _ml_shipment(items=[{"item_id": "MCO1", "quantity": 2}])

        self.assertEqual(self._process("2001"), CLAIMED_ELSEWHERE)
        create_order.assert_not_called()

    def test_mercadolibre_failure_keeps_a_marker_for_recovery(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        from integrations.mercadolibre.client import MercadoLibreAPIError

        get_order.side_effect = MercadoLibreAPIError(500, "boom")

        with self.assertRaises(MercadoLibreAPIError):
            self._process()

        marker = MarketplaceOrder.objects.get(marketplace_order_id="2001")
        self.assertEqual(marker.status, MarketplaceOrder.Status.PENDING)
        self.assertIn("MercadoLibreAPIError", marker.error_description)

    def test_sku_error_is_retried_without_asking_billing_again(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        variant.return_value = None
        self.assertEqual(self._process(), MarketplaceOrder.Status.ERROR_ORDER)

        variant.return_value = _inventory()
        self.assertEqual(self._process(), MarketplaceOrder.Status.CREATED)
        get_billing.assert_called_once()
        self.assertEqual(MarketplaceOrder.objects.get().items.count(), 1)

    def test_fails_before_touching_anything_without_fixed_customer(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        with patch(ML_CUSTOMER_PATCH, ""), self.assertRaises(ValueError):
            self._process()
        self.assertFalse(MarketplaceOrder.objects.exists())
        get_order.assert_not_called()

    def test_falabella_batch_ignores_mercadolibre_orders(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        MarketplaceOrder.objects.create(marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE, marketplace_order_id="2001")

        with patch(FIXED_CUSTOMER_PATCH, "999"):
            process_pending_orders()

        create_order.assert_not_called()

    def test_order_claimed_by_another_notification_meanwhile_is_not_created_twice(self, get_order, get_shipment, get_pack, get_billing, variant, create_order):
        # Regresión del 2026-09-25 (pedido 2000018635989514): otra
        # notificación reclama la fila mientras esta consulta la facturación;
        # guardar los datos no debe devolverla a `pending`.
        from .functions.process_mercadolibre_order import CLAIMED_ELSEWHERE

        def claimed_meanwhile(billing_info_id):
            MarketplaceOrder.objects.filter(marketplace_order_id="2001").update(
                status=MarketplaceOrder.Status.PROCESSING
            )
            return _ml_billing()

        get_billing.side_effect = claimed_meanwhile

        self.assertEqual(self._process(), CLAIMED_ELSEWHERE)
        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.status, MarketplaceOrder.Status.PROCESSING)
        self.assertFalse(order.items.exists())  # los ítems los crea quien reclamó
        create_order.assert_not_called()


RECOVER = "orders.functions.recover_mercadolibre_orders"


@patch(f"{RECOVER}.process_mercadolibre_order", return_value=MarketplaceOrder.Status.CREATED)
@patch(f"{RECOVER}.get_missed_feeds", return_value=[])
class RecoverMercadoLibreOrdersTests(TestCase):
    def _row(self, order_id, minutes_ago, **fields):
        from datetime import timedelta

        from django.utils import timezone

        row = MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE, marketplace_order_id=order_id, **fields
        )
        past = timezone.now() - timedelta(minutes=minutes_ago)
        MarketplaceOrder.objects.filter(pk=row.pk).update(updated_at=past, created_at=past)
        return row

    def _recover(self):
        from .functions.recover_mercadolibre_orders import recover_mercadolibre_orders

        recover_mercadolibre_orders()

    def test_processes_missed_feeds_and_stale_local_orders(self, missed, process):
        missed.return_value = [{"resource": "/orders/3001", "topic": "orders_v2"}, {"resource": "/items/1"}]
        self._row("3002", minutes_ago=60)
        self._row("3003", minutes_ago=60, status=MarketplaceOrder.Status.ERROR_ORDER)

        self._recover()

        self.assertEqual([c.args[0] for c in process.call_args_list], ["3001", "3002", "3003"])

    def test_skips_recent_processing_created_and_falabella_orders(self, missed, process):
        self._row("3001", minutes_ago=1)
        self._row("3002", minutes_ago=60, status=MarketplaceOrder.Status.PROCESSING)
        self._row("3003", minutes_ago=60, shopify_order_id="777", status=MarketplaceOrder.Status.CREATED)
        MarketplaceOrder.objects.create(marketplace=MarketplaceOrder.Marketplace.FALABELLA, marketplace_order_id="3004")

        self._recover()

        process.assert_not_called()

    def test_one_failure_does_not_stop_the_rest_but_marks_the_run_as_error(self, missed, process):
        self._row("3001", minutes_ago=60)
        self._row("3002", minutes_ago=60)
        process.side_effect = [RuntimeError("caído"), MarketplaceOrder.Status.CREATED]

        with self.assertRaisesMessage(RuntimeError, "3001"):
            self._recover()
        self.assertEqual(process.call_count, 2)

    def test_reports_packs_that_stay_incomplete(self, missed, process):
        from .functions.recover_mercadolibre_orders import INCOMPLETE_REPORT_AFTER
        from .functions.process_mercadolibre_order import INCOMPLETE

        process.return_value = INCOMPLETE
        self._row(
            "3001",
            minutes_ago=INCOMPLETE_REPORT_AFTER.total_seconds() / 60 + 5,
            error_description="Envío incompleto: órdenes del pack sin pagar 3002",
        )
        steps = []

        from .functions.recover_mercadolibre_orders import recover_mercadolibre_orders

        recover_mercadolibre_orders(progress_callback=lambda percent, step=None: steps.append(step))

        self.assertIn("3001", steps[-1])


WEBHOOK_URL = "/api/orders/webhooks/mercadolibre/"


@patch("orders.webhooks.MERCADOLIBRE_CLIENT_ID", "APP")
@patch("orders.webhooks.get_connected_seller_id", return_value="82071021")
@patch("orders.webhooks.launch_process")
class MercadoLibreWebhookTests(TestCase):
    def _payload(self, **overrides):
        payload = {
            "_id": "abc",
            "resource": "/orders/2001",
            "user_id": 82071021,
            "topic": "orders_v2",
            "application_id": "APP",
            "attempts": 1,
        }
        payload.update(overrides)
        return payload

    def test_valid_notification_launches_the_process(self, launch, seller):
        response = self.client.post(WEBHOOK_URL, self._payload(), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        launch.assert_called_once_with(
            code="orders.process_mercadolibre_notification", params={"order_id": "2001"}
        )

    def test_invalid_notifications_answer_200_without_launching(self, launch, seller):
        for overrides in (
            {"application_id": "OTRA"},
            {"user_id": 1},
            {"topic": "items"},
            {"resource": "/orders/2001/shipments"},
        ):
            response = self.client.post(WEBHOOK_URL, self._payload(**overrides), content_type="application/json")
            self.assertEqual(response.status_code, 200)
        launch.assert_not_called()

    def test_without_a_connected_account_nothing_is_launched(self, launch, seller):
        seller.return_value = ""

        self.client.post(WEBHOOK_URL, self._payload(), content_type="application/json")

        launch.assert_not_called()


MADECENTRO_WEBHOOK_URL = "/api/orders/webhooks/madecentro/"


@patch("orders.webhooks.launch_process")
class MadecentroWebhookCaptureTests(TestCase):
    """Fase 0: el webhook solo registra lo que llega y responde 200."""

    def test_anonymous_json_is_logged_and_answered_200(self, launch):
        with self.assertLogs("orders.webhooks", level="WARNING") as logs:
            response = self.client.post(
                MADECENTRO_WEBHOOK_URL,
                {"order_id": 17707107, "event": "order/create"},
                content_type="application/json",
                HTTP_X_SHIPTURTLE_TOPIC="order/create",
            )

        self.assertEqual(response.status_code, 200)
        output = "\n".join(logs.output)
        self.assertIn("X-Shipturtle-Topic", output)
        self.assertIn("17707107", output)
        launch.assert_not_called()

    def test_non_json_body_is_accepted(self, launch):
        with self.assertLogs("orders.webhooks", level="WARNING") as logs:
            response = self.client.post(MADECENTRO_WEBHOOK_URL, "no es json", content_type="text/plain")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no es json", "\n".join(logs.output))

    @patch("orders.webhooks.MADECENTRO_CAPTURE_MAX_BYTES", 10)
    def test_large_body_is_truncated_in_the_log(self, launch):
        with self.assertLogs("orders.webhooks", level="WARNING") as logs:
            self.client.post(MADECENTRO_WEBHOOK_URL, "A" * 10 + "COLA", content_type="text/plain")

        output = "\n".join(logs.output)
        self.assertIn("truncated=True", output)
        self.assertNotIn("COLA", output)


# ---------------------------------------------------------------- Madecentro

MC = "orders.functions.process_madecentro_order"
MC_IMPORT = "orders.functions.import_madecentro_orders"


def _mc_order(order_id="17707107", financial_status="paid", cancelled=False, items=None):
    return {
        "order_id": order_id,
        "order_number": "1001114236",
        "financial_status": financial_status,
        "cancelled": cancelled,
        "customer_first_name": "Ana",
        "customer_last_name": "Gómez",
        "customer_email": "ana@example.com",
        "customer_phone": "3001234567",
        "customer_address": "Calle 1 # 2-3",
        "customer_city": "Medellín",
        "customer_region": "Antioquia",
        "items": [{"sku": "SKU-1", "quantity": 2, "price": "221335"}] if items is None else items,
    }


@patch(PRIORITY_LOCATION_PATCH, "97615380757")
@patch(f"{MC}.MADECENTRO_SHOPIFY_CUSTOMER_ID", "666")
@patch(SHIPMENT_CREATE_ORDER, return_value={"order_id": "777", "order_name": "#1001"})
@patch(SHIPMENT_VARIANT, return_value=_inventory())
@patch(f"{MC}.get_order", return_value=_mc_order())
class ProcessMadecentroOrderTests(NoExistingShopifyOrderMixin, TestCase):
    def _process(self, order_id="17707107"):
        from .functions.process_madecentro_order import process_madecentro_order

        return process_madecentro_order(order_id)

    def test_paid_order_is_persisted_and_created_for_the_fixed_customer(self, get_order, variant, create_order):
        self.assertEqual(self._process(), MarketplaceOrder.Status.CREATED)

        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.marketplace, MarketplaceOrder.Marketplace.MADECENTRO)
        self.assertEqual((order.marketplace_order_id, order.marketplace_order_number), ("17707107", "1001114236"))
        self.assertEqual((order.customer_email, order.customer_city), ("ana@example.com", "Medellín"))
        self.assertEqual((order.shopify_order_id, order.shopify_customer_id), ("777", "666"))
        item = order.items.get()
        self.assertEqual((item.marketplace_sku, item.quantity, item.unit_price), ("SKU-1", 2, "221335"))
        kwargs = create_order.call_args.kwargs
        self.assertEqual(kwargs["customer_id"], "666")
        self.assertEqual(kwargs["tags"], ["madecentro", "madecentro-1001114236"])
        self.assertIs(kwargs["requires_shipping"], True)
        self.assertEqual(kwargs["note"], "Madecentro #1001114236 — Ana Gómez")

    def test_created_order_is_not_read_again(self, get_order, variant, create_order):
        from .functions.process_madecentro_order import ALREADY_HANDLED

        self._process()
        get_order.reset_mock()

        self.assertEqual(self._process(), ALREADY_HANDLED)
        get_order.assert_not_called()
        create_order.assert_called_once()

    def test_order_in_processing_is_left_alone(self, get_order, variant, create_order):
        from .functions.process_madecentro_order import ALREADY_HANDLED

        MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MADECENTRO,
            marketplace_order_id="17707107",
            status=MarketplaceOrder.Status.PROCESSING,
        )

        self.assertEqual(self._process(), ALREADY_HANDLED)
        get_order.assert_not_called()
        create_order.assert_not_called()

    def test_unpaid_or_cancelled_orders_leave_nothing_behind(self, get_order, variant, create_order):
        from .functions.process_madecentro_order import NOT_PAID

        for data in (_mc_order(financial_status="voided"), _mc_order(cancelled=True)):
            MarketplaceOrder.objects.create(
                marketplace=MarketplaceOrder.Marketplace.MADECENTRO, marketplace_order_id="17707107"
            )
            get_order.return_value = data
            self.assertEqual(self._process(), NOT_PAID)
            self.assertFalse(MarketplaceOrder.objects.exists())
        create_order.assert_not_called()

    def test_unknown_sku_marks_error_and_a_retry_creates_it(self, get_order, variant, create_order):
        variant.return_value = None
        self.assertEqual(self._process(), MarketplaceOrder.Status.ERROR_ORDER)
        self.assertIn("SKU-1", MarketplaceOrder.objects.get().error_description)

        variant.return_value = _inventory()
        self.assertEqual(self._process(), MarketplaceOrder.Status.CREATED)
        self.assertEqual(MarketplaceOrder.objects.get().items.count(), 1)  # los ítems no se duplican

    def test_api_failure_is_recorded_on_the_existing_row_and_raised(self, get_order, variant, create_order):
        MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MADECENTRO,
            marketplace_order_id="17707107",
            status=MarketplaceOrder.Status.ERROR_ORDER,
        )
        get_order.side_effect = RuntimeError("MADECENTRO_HTTP_500")

        with self.assertRaises(RuntimeError):
            self._process()
        self.assertIn("MADECENTRO_HTTP_500", MarketplaceOrder.objects.get().error_description)

    def test_marketplace_sku_is_translated_with_the_catalog(self, get_order, variant, create_order):
        from products.models import MarketplaceSku, Product

        product = Product.objects.create(sku="PAC8424")
        MarketplaceSku.objects.create(product=product, marketplace="madecentro", sku="SKU-1")
        # La misma equivalencia en otro canal no aplica a Madecentro.
        MarketplaceSku.objects.create(product=Product.objects.create(sku="OTRO"), marketplace="sodimac", sku="SKU-1")

        self.assertEqual(self._process(), MarketplaceOrder.Status.CREATED)
        variant.assert_called_once_with("PAC8424")
        self.assertEqual(MarketplaceOrder.objects.get().items.get().marketplace_sku, "SKU-1")

    def test_translated_sku_missing_in_shopify_names_both_skus(self, get_order, variant, create_order):
        from products.models import MarketplaceSku, Product

        MarketplaceSku.objects.create(
            product=Product.objects.create(sku="PAC8424"), marketplace="madecentro", sku="SKU-1"
        )
        variant.return_value = None

        self.assertEqual(self._process(), MarketplaceOrder.Status.ERROR_ORDER)
        self.assertEqual(
            MarketplaceOrder.objects.get().error_description, "SKU no encontrado en Shopify: SKU-1 (equivalencia PAC8424)"
        )

    def test_sku_that_is_a_kit_goes_as_one_line_per_component_with_the_price_split(
        self, get_order, variant, create_order
    ):
        from products.models import KitComponent, MarketplaceSku, Product

        kit = Product.objects.create(sku="KIT-1", is_kit=True)
        KitComponent.objects.create(kit=kit, component=Product.objects.create(sku="PAC8424"), quantity=2)
        KitComponent.objects.create(kit=kit, component=Product.objects.create(sku="PAC9999"), quantity=1)
        MarketplaceSku.objects.create(product=kit, marketplace="madecentro", sku="SKU-1")
        variant.side_effect = lambda sku: _inventory(variant_id=f"v-{sku}", sku=sku)

        # 2 kits a 221335: 3 unidades de componente por kit -> 73778.33 c/u.
        self.assertEqual(self._process(), MarketplaceOrder.Status.CREATED)
        _, kwargs = create_order.call_args
        self.assertEqual(
            kwargs["items"],
            [
                {"variant_id": "v-PAC8424", "quantity": 4, "price": "73778.33"},
                {"variant_id": "v-PAC9999", "quantity": 2, "price": "73778.33"},
            ],
        )
        item = MarketplaceOrder.objects.get().items.get()
        self.assertEqual(item.shopify_variant_id, "")
        self.assertEqual([line["sku"] for line in item.inventory_snapshot], ["PAC8424", "PAC9999"])

    def test_kit_component_missing_in_shopify_names_the_kit(self, get_order, variant, create_order):
        from products.models import KitComponent, MarketplaceSku, Product

        kit = Product.objects.create(sku="KIT-1", is_kit=True)
        KitComponent.objects.create(kit=kit, component=Product.objects.create(sku="PAC8424"), quantity=2)
        MarketplaceSku.objects.create(product=kit, marketplace="madecentro", sku="SKU-1")
        variant.return_value = None

        self.assertEqual(self._process(), MarketplaceOrder.Status.ERROR_ORDER)
        self.assertEqual(
            MarketplaceOrder.objects.get().error_description,
            "SKU no encontrado en Shopify: SKU-1 (componente PAC8424 del kit KIT-1)",
        )
        create_order.assert_not_called()

    def test_empty_kit_marks_error_without_calling_shopify(self, get_order, variant, create_order):
        from products.models import MarketplaceSku, Product

        MarketplaceSku.objects.create(
            product=Product.objects.create(sku="KIT-1", is_kit=True), marketplace="madecentro", sku="SKU-1"
        )

        self.assertEqual(self._process(), MarketplaceOrder.Status.ERROR_ORDER)
        self.assertIn("KIT-1 no tiene componentes", MarketplaceOrder.objects.get().error_description)
        variant.assert_not_called()
        create_order.assert_not_called()

    def test_order_claimed_by_another_process_meanwhile_is_not_created_twice(self, get_order, variant, create_order):
        from .functions.process_madecentro_order import CLAIMED_ELSEWHERE

        MarketplaceOrder.objects.create(
            marketplace=MarketplaceOrder.Marketplace.MADECENTRO, marketplace_order_id="17707107"
        )

        def claimed_meanwhile(order_id):
            MarketplaceOrder.objects.filter(marketplace_order_id=order_id).update(
                status=MarketplaceOrder.Status.PROCESSING
            )
            return _mc_order()

        get_order.side_effect = claimed_meanwhile

        self.assertEqual(self._process(), CLAIMED_ELSEWHERE)
        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.status, MarketplaceOrder.Status.PROCESSING)
        self.assertFalse(order.items.exists())
        create_order.assert_not_called()

    def test_without_fixed_customer_fails_before_reading(self, get_order, variant, create_order):
        with patch(f"{MC}.MADECENTRO_SHOPIFY_CUSTOMER_ID", ""):
            with self.assertRaises(ValueError):
                self._process()
        get_order.assert_not_called()


@patch(f"{MC_IMPORT}.process_madecentro_order", return_value=MarketplaceOrder.Status.CREATED)
@patch(f"{MC_IMPORT}.list_orders")
class ImportMadecentroOrdersTests(TestCase):
    def _listing(self, *statuses, count=None):
        orders = [
            {"order_id": str(index), "order_number": str(index), "financial_status": status}
            for index, status in enumerate(statuses, start=1)
        ]
        return {"orders": orders, "count": len(orders) if count is None else count}

    def _run(self, params=None):
        from .functions.import_madecentro_orders import import_madecentro_orders

        import_madecentro_orders(params=params)

    def test_only_paid_orders_are_processed_and_default_range_is_one_day(self, list_orders, process):
        from django.utils import timezone

        list_orders.return_value = self._listing("paid", "voided", "paid")

        self._run()

        self.assertEqual([call.args[0] for call in process.call_args_list], ["1", "3"])
        start, end = list_orders.call_args.args
        self.assertEqual(end, timezone.localdate())
        self.assertEqual((end - start).days, 1)

    def test_days_param_widens_the_range_and_all_pages_are_read(self, list_orders, process):
        page_2 = {"orders": [{"order_id": "2", "order_number": "2", "financial_status": "paid"}], "count": 2}
        list_orders.side_effect = [self._listing("paid", count=2), page_2]

        self._run({"days": 7})

        self.assertEqual(list_orders.call_count, 2)
        start, end = list_orders.call_args.args
        self.assertEqual((end - start).days, 7)
        self.assertEqual([call.args[0] for call in process.call_args_list], ["1", "2"])

    def test_stored_pending_or_failed_orders_are_retried_without_duplicates(self, list_orders, process):
        for order_id, status in (
            ("1", MarketplaceOrder.Status.ERROR_ORDER),  # también en el listado
            ("50", MarketplaceOrder.Status.PENDING),  # fuera del rango
            ("60", MarketplaceOrder.Status.PROCESSING),  # revisión manual
            ("70", MarketplaceOrder.Status.CREATED),
        ):
            MarketplaceOrder.objects.create(
                marketplace=MarketplaceOrder.Marketplace.MADECENTRO,
                marketplace_order_id=order_id,
                status=status,
                shopify_order_id="9" if status == MarketplaceOrder.Status.CREATED else "",
            )
        MarketplaceOrder.objects.create(marketplace=MarketplaceOrder.Marketplace.FALABELLA, marketplace_order_id="80")
        list_orders.return_value = self._listing("paid")

        self._run()

        self.assertEqual([call.args[0] for call in process.call_args_list], ["1", "50"])

    def test_limit_caps_the_orders_processed(self, list_orders, process):
        list_orders.return_value = self._listing("paid", "paid", "paid")

        self._run({"limit": 1})

        process.assert_called_once_with("1")

    def test_a_failure_does_not_stop_the_rest_but_fails_the_run(self, list_orders, process):
        list_orders.return_value = self._listing("paid", "paid")
        process.side_effect = [RuntimeError("MADECENTRO_HTTP_500"), MarketplaceOrder.Status.CREATED]

        with self.assertRaisesRegex(RuntimeError, "fallaron 1: 1"):
            self._run()
        self.assertEqual(process.call_count, 2)

    def test_process_type_seeded_by_migration(self, list_orders, process):
        from orchestrator.models import ProcessType

        self.assertFalse(ProcessType.objects.get(code="orders.import_madecentro").allow_concurrent)


SODI = "orders.functions.sync_sodimac_orders"


def _sodi_row(oc="9001", status="1-PENDIENTE", transmitted_at="2026-10-02", sku="54654", quantity=2, cost="1000.50"):
    return {
        "purchase_order": oc,
        "status": status,
        "transmitted_at": transmitted_at,
        "sku": sku,
        "quantity": quantity,
        "cost": Decimal(cost),
        "cost_with_vat": Decimal(cost) * Decimal("1.19"),
    }


def _queue(type_1=None, type_4=None):
    """Simula la cola destructiva: cada tipo devuelve sus filas una vez."""
    pending = {"1": list(type_1 or []), "4": list(type_4 or [])}

    def get_orders(order_type):
        rows, pending[order_type] = pending[order_type], []
        return rows

    return get_orders


@patch(PRIORITY_LOCATION_PATCH, "97615380757")
@patch(f"{SODI}.SODIMAC_SHOPIFY_CUSTOMER_ID", "7247084421397")
@patch(f"{SODI}.SODIMAC_CUTOVER_DATE", "2026-10-02")
@patch(f"{SODI}.SODIMAC_LEGACY_OCS", [])
@patch(f"{SODI}.run_sodimac_invoicing", return_value={"invoiced": 0})
@patch(SHIPMENT_CREATE_ORDER, return_value={"order_id": "777", "order_name": "#1001"})
@patch(SHIPMENT_VARIANT, return_value=_inventory())
@patch(f"{SODI}.reinject_order")
@patch(f"{SODI}.get_orders")
class SyncSodimacOrdersTests(NoExistingShopifyOrderMixin, TestCase):
    def _sync(self, params=None):
        from .functions.sync_sodimac_orders import sync_sodimac_orders

        return sync_sodimac_orders(params)

    def _order(self, oc="9001", **overrides):
        defaults = dict(
            marketplace=MarketplaceOrder.Marketplace.SODIMAC,
            marketplace_order_id=oc,
            marketplace_order_number=oc,
            marketplace_status="1-PENDIENTE",
            status=MarketplaceOrder.Status.CREATED,
            shopify_order_id="555",
        )
        defaults.update(overrides)
        order = MarketplaceOrder.objects.create(**defaults)
        MarketplaceOrderItem.objects.create(order=order, marketplace_sku="54654", quantity=1, unit_price="10")
        return order

    def test_new_oc_is_saved_and_created_in_shopify_as_pending_payment(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(
            type_1=[_sodi_row(sku="54654", quantity=2), _sodi_row(sku="777", quantity=1, cost="20")]
        )

        self._sync()

        order = MarketplaceOrder.objects.get()
        self.assertEqual((order.marketplace, order.marketplace_order_id), ("sodimac", "9001"))
        self.assertEqual(order.marketplace_status, "1-PENDIENTE")
        self.assertEqual(timezone.localtime(order.marketplace_created_at).date().isoformat(), "2026-10-02")
        self.assertEqual(
            list(order.items.order_by("pk").values_list("marketplace_sku", "quantity", "unit_price")),
            [("54654", 2, "1000.50"), ("777", 1, "20")],
        )
        self.assertEqual((order.status, order.shopify_order_id), (MarketplaceOrder.Status.CREATED, "777"))
        _, kwargs = create_order.call_args
        self.assertEqual(kwargs["financial_status"], "PENDING")
        self.assertEqual(kwargs["customer_id"], "7247084421397")
        self.assertEqual(kwargs["note"], "Sodimac #9001")
        self.assertEqual(kwargs["tags"], ["sodimac", "sodimac-9001"])
        self.assertIs(kwargs["requires_shipping"], True)
        pamo_web.assert_called_once()

    def test_oc_is_saved_before_shopify_even_if_shopify_rejects_it(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row()])
        create_order.side_effect = ShopifyOrderCreationError([{"message": "boom"}])

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)
        self.assertTrue(order.items.exists())
        self.assertIn("Shopify 9001", str(raised.exception))
        pamo_web.assert_called_once()

    def test_known_oc_only_updates_its_status_without_duplicating_items(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        self._order()
        get_orders.side_effect = _queue(type_4=[_sodi_row(status="4-ESTADO FINAL")])

        self._sync()

        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.marketplace_status, "4-ESTADO FINAL")
        self.assertEqual(order.items.count(), 1)
        create_order.assert_not_called()

    def test_same_oc_in_both_order_types_is_saved_once(self, get_orders, reinject, variant, create_order, pamo_web):
        get_orders.side_effect = _queue(type_1=[_sodi_row()], type_4=[_sodi_row(status="3-EN TRANSPORTE")])

        self._sync()

        order = MarketplaceOrder.objects.get()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.marketplace_status, "3-EN TRANSPORTE")
        create_order.assert_called_once()

    def test_reinjects_only_open_ocs(self, get_orders, reinject, variant, create_order, pamo_web):
        self._order("1", marketplace_status="1-PENDIENTE")
        self._order("3", marketplace_status="3-EN TRANSPORTE")
        self._order("4", marketplace_status="4-ESTADO FINAL")
        self._order("ML", marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE, marketplace_status="")
        get_orders.side_effect = _queue()

        self._sync()

        self.assertEqual(sorted(call.args[0] for call in reinject.call_args_list), ["1", "3"])

    def test_a_reinjection_failure_does_not_stop_the_rest(self, get_orders, reinject, variant, create_order, pamo_web):
        self._order("1")
        self._order("2")
        reinject.side_effect = [RuntimeError("down"), None]
        get_orders.side_effect = _queue(type_1=[_sodi_row(oc="9001")])

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        self.assertIn("reinyección 1", str(raised.exception))
        self.assertTrue(MarketplaceOrder.objects.filter(marketplace_order_id="9001").exists())

    def test_oc_transmitted_before_the_cutover_goes_back_to_the_queue_for_pamo_web(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row(oc="8001", transmitted_at="01/10/2026 18:30:00")])

        self._sync()

        self.assertFalse(MarketplaceOrder.objects.exists())
        reinject.assert_called_once_with("8001")
        create_order.assert_not_called()

    def test_legacy_oc_of_the_cutover_day_goes_back_to_the_queue(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row(oc="8002"), _sodi_row(oc="9001")])

        with patch(f"{SODI}.SODIMAC_LEGACY_OCS", ["8002"]):
            self._sync()

        self.assertEqual(list(MarketplaceOrder.objects.values_list("marketplace_order_id", flat=True)), ["9001"])
        reinject.assert_called_once_with("8002")

    def test_day_first_transmission_date_is_understood(self, get_orders, reinject, variant, create_order, pamo_web):
        get_orders.side_effect = _queue(type_1=[_sodi_row(transmitted_at="02/10/2026 09:15:00")])

        self._sync()

        created_at = MarketplaceOrder.objects.get().marketplace_created_at
        self.assertEqual(timezone.localtime(created_at).strftime("%Y-%m-%d %H:%M"), "2026-10-02 09:15")

    def test_unknown_transmission_date_is_not_saved_and_goes_back_to_the_queue(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row(transmitted_at="ayer")])

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        self.assertFalse(MarketplaceOrder.objects.exists())
        reinject.assert_called_once_with("9001")
        self.assertIn("FECHA_TRANSMISION", str(raised.exception))

    def test_failure_reading_one_type_still_reads_the_other(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = [RuntimeError("timeout"), [_sodi_row()]]

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        self.assertTrue(MarketplaceOrder.objects.exists())
        self.assertIn("lectura tipo 1", str(raised.exception))

    def test_limit_caps_shopify_creation_but_saves_everything_read(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row(oc="1"), _sodi_row(oc="2"), _sodi_row(oc="3")])

        self._sync({"limit": 1})

        self.assertEqual(MarketplaceOrder.objects.count(), 3)
        self.assertEqual(create_order.call_count, 1)

    def test_retries_shopify_for_ocs_left_in_error(self, get_orders, reinject, variant, create_order, pamo_web):
        self._order(status=MarketplaceOrder.Status.ERROR_ORDER, shopify_order_id="", marketplace_status="4-ESTADO FINAL")
        self._order("2", status=MarketplaceOrder.Status.PROCESSING, shopify_order_id="")
        get_orders.side_effect = _queue()

        self._sync()

        self.assertEqual(MarketplaceOrder.objects.get(marketplace_order_id="9001").shopify_order_id, "777")
        self.assertEqual(MarketplaceOrder.objects.get(marketplace_order_id="2").status, MarketplaceOrder.Status.PROCESSING)
        create_order.assert_called_once()

    def test_pamo_web_failure_is_reported_after_everything_else_ran(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        get_orders.side_effect = _queue(type_1=[_sodi_row()])
        pamo_web.side_effect = RuntimeError("PAMO_WEB_HTTP_500")

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        self.assertEqual(MarketplaceOrder.objects.get().shopify_order_id, "777")
        self.assertIn("pamo_web", str(raised.exception))

    def test_ocs_pamo_web_could_not_give_back_are_reported(self, get_orders, reinject, variant, create_order, pamo_web):
        get_orders.side_effect = _queue()
        pamo_web.return_value = {"success": True, "read": ["9100"], "not_returned": ["9100"], "invoiced": []}

        with self.assertRaises(RuntimeError) as raised:
            self._sync()

        self.assertIn("pamo_web no devolvió a la cola: 9100", str(raised.exception))

    def test_fails_before_reading_without_fixed_customer_or_cutover(
        self, get_orders, reinject, variant, create_order, pamo_web
    ):
        with patch(f"{SODI}.SODIMAC_SHOPIFY_CUSTOMER_ID", ""):
            with self.assertRaises(RuntimeError):
                self._sync()
        with patch(f"{SODI}.SODIMAC_CUTOVER_DATE", ""):
            with self.assertRaises(RuntimeError):
                self._sync()
        get_orders.assert_not_called()
        reinject.assert_not_called()

    def test_process_type_is_seeded(self, *mocks):
        from orchestrator.models import ProcessType

        process_type = ProcessType.objects.get(code="orders.sync_sodimac")
        self.assertFalse(process_type.allow_concurrent)


SYNC = "orders.functions.sync_shopify_orders"


def _shopify_data(order_id, **overrides):
    """Pedido normalizado como lo entrega `integrations.shopify` (forma de
    `normalize_order`)."""
    data = {
        "id": order_id, "name": f"N{order_id}", "created_at": "2026-10-03T15:00:00Z",
        "updated_at": "2026-10-03T15:00:00Z", "cancelled_at": None,
        "financial_status": "PAID", "fulfillment_status": "UNFULFILLED", "tags": [],
        "email": "web@example.com", "phone": "300", "total": "200.00", "currency": "COP",
        "customer": {"id": "9", "first_name": "Cliente", "last_name": "Web", "identification": "900",
                     "city": "Medellín", "region": "Antioquia", "address": "Calle 1"},
        "line_items": [{"sku": "A1", "name": "Silla", "quantity": 2, "unit_price": "100.0"}],
        "line_items_truncated": False,
    }
    data.update(overrides)
    return data


def _page(*orders, has_next=False, cursor="cur"):
    return {"orders": list(orders), "has_next_page": has_next, "end_cursor": cursor if has_next else None}


def _row(order_id, *, marketplace=MarketplaceOrder.Marketplace.FALABELLA, shopify_order_id="", number=None, **fields):
    return MarketplaceOrder.objects.create(
        marketplace=marketplace, marketplace_order_id=order_id, marketplace_order_number=number or order_id,
        shopify_order_id=shopify_order_id, **fields,
    )


class UpsertShopifyOrderTests(TestCase):
    def _upsert(self, data):
        from .functions.upsert_shopify_order import upsert_shopify_order

        return upsert_shopify_order(data)

    def test_creates_order_with_lines_and_marketplace_from_tags(self):
        result = self._upsert(_shopify_data("10", tags=["SODIMAC"], cancelled_at="2026-10-04T10:00:00Z"))

        self.assertEqual(result, "created")
        order = ShopifyOrder.objects.get(shopify_id="10")
        self.assertEqual(order.marketplace, "sodimac")
        self.assertEqual(order.total, Decimal("200.00"))
        self.assertEqual(order.customer_identification, "900")
        self.assertIsNotNone(order.cancelled_at)
        line = order.lines.get()
        self.assertEqual((line.sku, line.quantity, line.unit_price, line.line_total), ("A1", 2, Decimal("100.00"), Decimal("200.00")))

    def test_update_replaces_lines(self):
        self._upsert(_shopify_data("10"))
        lines = [{"sku": "B", "name": "", "quantity": 1, "unit_price": "5"}, {"sku": "C", "name": "", "quantity": 3, "unit_price": ""}]

        result = self._upsert(_shopify_data("10", updated_at="2026-10-04T15:00:00Z", financial_status="REFUNDED", line_items=lines))

        self.assertEqual(result, "updated")
        order = ShopifyOrder.objects.get(shopify_id="10")
        self.assertEqual(order.financial_status, "REFUNDED")
        self.assertEqual([(line.sku, line.line_total) for line in order.lines.all()], [("B", Decimal("5.00")), ("C", None)])

    def test_older_data_does_not_overwrite(self):
        self._upsert(_shopify_data("10", updated_at="2026-10-04T15:00:00Z", financial_status="PAID"))

        result = self._upsert(_shopify_data("10", updated_at="2026-10-03T15:00:00Z", financial_status="PENDING"))

        self.assertEqual(result, "skipped")
        self.assertEqual(ShopifyOrder.objects.get(shopify_id="10").financial_status, "PAID")

    def test_deleted_order_is_not_revived(self):
        from .functions.mark_shopify_order_deleted import mark_shopify_order_deleted

        mark_shopify_order_deleted("10")

        self.assertEqual(self._upsert(_shopify_data("10")), "skipped")
        order = ShopifyOrder.objects.get(shopify_id="10")
        self.assertIsNotNone(order.deleted_at)
        self.assertFalse(order.lines.exists())

    @patch("orders.functions.upsert_shopify_order.get_order")
    def test_truncated_order_is_read_again(self, get_order):
        full = [{"sku": f"S{index}", "name": "", "quantity": 1, "unit_price": "1"} for index in range(35)]
        get_order.return_value = _shopify_data("10", line_items=full)

        self._upsert(_shopify_data("10", line_items_truncated=True))

        get_order.assert_called_once_with("10")
        self.assertEqual(ShopifyOrder.objects.get(shopify_id="10").lines.count(), 35)

    @patch("orders.functions.upsert_shopify_order.get_order", return_value=None)
    def test_truncated_order_missing_in_shopify_is_marked_deleted(self, get_order):
        self.assertEqual(self._upsert(_shopify_data("10", line_items_truncated=True)), "deleted")
        self.assertIsNotNone(ShopifyOrder.objects.get(shopify_id="10").deleted_at)

    def test_concurrent_creation_retries_once(self):
        from django.db import IntegrityError

        from .functions import upsert_shopify_order as module

        real = module._upsert
        calls = []

        def racing(data):
            calls.append(data["id"])
            if len(calls) == 1:
                raise IntegrityError("duplicate key")
            return real(data)

        with patch.object(module, "_upsert", side_effect=racing):
            self.assertEqual(module.upsert_shopify_order(_shopify_data("10")), "created")
        self.assertEqual(len(calls), 2)


class NoWebDispatchAssignmentMixin:
    """El webhook y la reconciliación asignan bodega a pedidos web pagados
    (`assign_web_dispatch`, probado en `tests_dispatch.py`); aquí no
    interesa y consultaría el inventario de Shopify."""

    def setUp(self):
        super().setUp()
        for target in (
            "orders.functions.process_shopify_order_webhook.assign_web_dispatch",
            "orders.functions.sync_shopify_orders.assign_web_dispatch",
        ):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)


@patch("orders.functions.process_shopify_order_webhook.get_order")
class ProcessShopifyOrderWebhookTests(NoWebDispatchAssignmentMixin, TestCase):
    def _process(self, topic, order_id="10"):
        from .functions.process_shopify_order_webhook import process_shopify_order_webhook

        process_shopify_order_webhook({"topic": topic, "order_id": order_id})

    def test_create_and_update_read_the_order_again(self, get_order):
        get_order.return_value = _shopify_data("10", tags=["madecentro"])

        self._process("orders/create")
        get_order.return_value = _shopify_data("10", updated_at="2026-10-04T15:00:00Z", fulfillment_status="FULFILLED")
        self._process("orders/updated")

        order = ShopifyOrder.objects.get(shopify_id="10")
        self.assertEqual(order.fulfillment_status, "FULFILLED")
        self.assertEqual(get_order.call_count, 2)

    def test_delete_marks_without_reading(self, get_order):
        self._process("orders/delete")

        get_order.assert_not_called()
        self.assertIsNotNone(ShopifyOrder.objects.get(shopify_id="10").deleted_at)

    def test_order_missing_in_shopify_counts_as_deleted(self, get_order):
        get_order.return_value = None

        self._process("orders/updated")

        self.assertIsNotNone(ShopifyOrder.objects.get(shopify_id="10").deleted_at)


TEST_SHOPIFY_SECRET = "test-shopify-secret"


@patch("integrations.shopify.client.SHOPIFY_WEBHOOK_SECRET", TEST_SHOPIFY_SECRET)
@patch("orders.webhooks.launch_process")
class ShopifyOrderWebhookViewTests(TestCase):
    url = "/api/orders/webhooks/shopify/"

    def _post(self, body, topic="orders/create", signature=None):
        import base64
        import hashlib
        import hmac
        import json

        raw = json.dumps(body).encode()
        if signature is None:
            signature = base64.b64encode(hmac.new(TEST_SHOPIFY_SECRET.encode(), raw, hashlib.sha256).digest()).decode()
        return self.client.post(
            self.url, raw, content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature, HTTP_X_SHOPIFY_TOPIC=topic,
        )

    def test_valid_signature_launches_with_topic_and_id_only(self, launch):
        response = self._post({"id": 820982911946154508, "email": "x@example.com", "line_items": []}, topic="orders/updated")

        self.assertEqual(response.status_code, 200)
        launch.assert_called_once_with(
            code="orders.process_shopify_order_webhook",
            params={"topic": "orders/updated", "order_id": "820982911946154508"},
        )

    def test_invalid_or_missing_signature_is_403(self, launch):
        self.assertEqual(self._post({"id": 1}, signature="bad").status_code, 403)
        self.assertEqual(self._post({"id": 1}, signature="").status_code, 403)
        launch.assert_not_called()

    def test_unknown_topic_or_missing_id_is_200_without_launch(self, launch):
        self.assertEqual(self._post({"id": 1}, topic="orders/paid").status_code, 200)
        self.assertEqual(self._post({"name": "x"}).status_code, 200)
        launch.assert_not_called()


class _Cancelled(Exception):
    pass


class _CancelAfter:
    def __init__(self, pages):
        self.pages = pages

    def raise_if_cancelled(self):
        self.pages -= 1
        if self.pages <= 0:
            raise _Cancelled()


@patch(f"{SYNC}.list_orders_page")
class SyncShopifyOrdersTests(NoWebDispatchAssignmentMixin, TestCase):
    def _backfill(self, params=None, token=None):
        from .functions.backfill_shopify_orders import backfill_shopify_orders

        backfill_shopify_orders(params, cancellation_token=token)

    def _reconcile(self, token=None):
        from .functions.reconcile_shopify_orders import reconcile_shopify_orders

        reconcile_shopify_orders({}, cancellation_token=token)

    def _state(self):
        return ShopifyOrderSyncState.objects.filter(pk=1).values_list("last_reconciled_at", flat=True).first()

    def test_backfill_reads_all_pages_by_update_order(self, page):
        page.side_effect = [_page(_shopify_data("1"), has_next=True, cursor="c1"), _page(_shopify_data("2"))]

        self._backfill({"created_from": "2026-09-05"})

        self.assertEqual(ShopifyOrder.objects.count(), 2)
        first, second = page.call_args_list
        self.assertEqual(first.kwargs["query"], "created_at:>='2026-09-05T00:00:00-05:00'")
        self.assertEqual((first.kwargs["sort_key"], first.kwargs["reverse"]), ("UPDATED_AT", False))
        self.assertEqual(second.kwargs["after"], "c1")

    def test_backfill_defaults_to_30_days_and_seeds_checkpoint_once(self, page):
        page.return_value = _page()
        expected_day = (timezone.localdate() - timedelta(days=30)).isoformat()

        self._backfill()

        self.assertIn(f"created_at:>='{expected_day}T00:00:00-05:00'", page.call_args.kwargs["query"])
        seeded = self._state()
        self.assertIsNotNone(seeded)
        self._backfill()
        self.assertEqual(self._state(), seeded)

    def test_reconcile_without_checkpoint_uses_30_days(self, page):
        page.return_value = _page(_shopify_data("1"))
        before = timezone.now()

        self._reconcile()

        since = page.call_args.kwargs["query"]
        self.assertTrue(since.startswith("updated_at:>='"))
        self.assertEqual(ShopifyOrder.objects.count(), 1)
        self.assertGreaterEqual(self._state(), before)

    def test_reconcile_uses_checkpoint_with_overlap(self, page):
        from .functions.order_listing_format import COLOMBIA_TZ

        checkpoint = (timezone.now() - timedelta(hours=1)).astimezone(COLOMBIA_TZ).replace(microsecond=0)
        ShopifyOrderSyncState.objects.create(pk=1, last_reconciled_at=checkpoint)
        page.return_value = _page()

        self._reconcile()

        expected = (checkpoint - timedelta(minutes=15)).isoformat(timespec="seconds")
        self.assertEqual(page.call_args.kwargs["query"], f"updated_at:>='{expected}'")
        self.assertGreater(self._state(), checkpoint)

    def test_checkpoint_does_not_move_on_cancel_or_error(self, page):
        from .functions.order_listing_format import COLOMBIA_TZ

        checkpoint = timezone.datetime(2026, 10, 5, 12, 0, tzinfo=COLOMBIA_TZ)
        ShopifyOrderSyncState.objects.create(pk=1, last_reconciled_at=checkpoint)
        page.side_effect = [_page(_shopify_data("1"), has_next=True), _page(_shopify_data("2"))]

        with self.assertRaises(_Cancelled):
            self._reconcile(token=_CancelAfter(1))
        self.assertEqual(ShopifyOrder.objects.count(), 1)
        self.assertEqual(self._state(), checkpoint)

        page.side_effect = RuntimeError("Shopify caído")
        with self.assertRaises(RuntimeError):
            self._reconcile()
        self.assertEqual(self._state(), checkpoint)

    def test_process_types_are_seeded(self, page):
        from orchestrator.models import ProcessType

        types = {pt.code: pt.allow_concurrent for pt in ProcessType.objects.filter(code__contains="shopify_order")}
        self.assertEqual(
            types,
            {
                "orders.process_shopify_order_webhook": True,
                "orders.backfill_shopify_orders": False,
                "orders.reconcile_shopify_orders": False,
            },
        )


def _local_order(order_id, *, created_at=None, **overrides):
    """Guarda un pedido en la copia local por el mismo camino que la
    sincronización."""
    from .functions.upsert_shopify_order import upsert_shopify_order

    upsert_shopify_order(_shopify_data(order_id, **overrides))
    if created_at:
        ShopifyOrder.objects.filter(shopify_id=order_id).update(shopify_created_at=created_at)


class ListOrdersNotCreatedTests(TestCase):
    def _list(self, **kwargs):
        from .functions.list_orders_not_created import list_orders_not_created

        return list_orders_not_created(**kwargs)

    def test_includes_alert_statuses_and_excludes_the_rest(self):
        Status = MarketplaceOrder.Status
        _row("E1", status=Status.ERROR_ORDER, error_description="SKU no encontrado en Shopify: X")
        _row("E2", status=Status.ERROR_CUSTOMER)
        _row("E3", status=Status.PROCESSING)
        _row("E4", status=Status.PENDING, error_description="Envío incompleto")
        _row("OK1", status=Status.PENDING)
        _row("OK2", status=Status.CREATED, shopify_order_id="1")
        _row("OK3", status=Status.ERROR_ORDER, shopify_order_id="2", error_description="viejo")

        result = self._list()

        numbers = {order["marketplace_order_number"] for order in result["orders_not_created"]}
        self.assertEqual(numbers, {"E1", "E2", "E3", "E4"})
        self.assertEqual(result["orders_not_created_count"], 4)

    def test_error_items_and_total(self):
        row = _row("E1", status=MarketplaceOrder.Status.ERROR_ORDER,
                   error_description="SKU no encontrado en Shopify: X", customer_first_name="Ana")
        MarketplaceOrderItem.objects.create(order=row, marketplace_sku="X", quantity=2, unit_price="100")
        MarketplaceOrderItem.objects.create(order=row, marketplace_sku="Y", quantity=1, unit_price="50.5")

        order = self._list()["orders_not_created"][0]

        self.assertEqual(order["error"], "SKU no encontrado en Shopify: X")
        self.assertEqual(order["status"], "error_creando_orden")
        self.assertEqual(order["customer"]["first_name"], "Ana")
        self.assertEqual(order["fulfillment"]["status"], "")
        self.assertEqual(order["items"][0], {"sku": "X", "quantity": 2, "unit_price": "100.00", "line_total": "200.00"})
        self.assertEqual(order["total"], "250.50")

    def test_filters(self):
        Status = MarketplaceOrder.Status
        _row("F1", status=Status.ERROR_ORDER)
        old = _row("F2", status=Status.ERROR_ORDER)
        MarketplaceOrder.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        _row("M1", marketplace=MarketplaceOrder.Marketplace.MADECENTRO, status=Status.ERROR_ORDER)

        def numbers(**kwargs):
            return {order["marketplace_order_number"] for order in self._list(**kwargs)["orders_not_created"]}

        today = timezone.localdate()
        self.assertEqual(numbers(marketplace="madecentro"), {"M1"})
        self.assertEqual(numbers(date_from=today, date_to=today), {"F1", "M1"})
        self.assertEqual(numbers(search="#F2"), {"F2"})
        self.assertEqual(numbers(marketplace="shopify"), set())

    def test_limit_keeps_real_count_without_n_plus_one(self):
        for index in range(4):
            row = _row(f"E{index}", status=MarketplaceOrder.Status.ERROR_ORDER)
            MarketplaceOrderItem.objects.create(order=row, marketplace_sku="X", quantity=1, unit_price="1")

        with self.assertNumQueries(3):  # filas, ítems (prefetch) y conteo
            result = self._list(limit=2)

        self.assertEqual(len(result["orders_not_created"]), 2)
        self.assertEqual(result["orders_not_created_count"], 4)


@patch("integrations.shopify.client.requests.post", side_effect=AssertionError("el listado no debe llamar a Shopify"))
class OrderListAPITests(TestCase):
    url = "/api/orders/"

    def setUp(self):
        self.admin = User.objects.create_user("admin", password="x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.other = User.objects.create_user("other", password="x")

    def _get(self, params=None):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, params or {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_reads_only_local_data_with_both_lists(self, shopify):
        _local_order("10", tags=["falabella"])
        _row("FA-1", number="3254", shopify_order_id="10", customer_identification="1020", customer_first_name="Ana",
             fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD, fulfillment_note="Ninguna bodega cubre el pedido")
        _row("E1", status=MarketplaceOrder.Status.ERROR_ORDER, error_description="SKU no encontrado en Shopify: X")

        body = self._get()

        shopify.assert_not_called()
        self.assertEqual(body["count"], 1)
        order = body["orders"][0]
        self.assertEqual(order["marketplace"], "falabella")
        self.assertEqual(order["marketplace_order_numbers"], ["3254"])
        self.assertEqual(order["customer"]["identification"], "1020")
        self.assertEqual(order["fulfillment"]["status"], "novedad")
        self.assertEqual(order["fulfillment"]["note"], "Ninguna bodega cubre el pedido")
        self.assertEqual(
            order["items"], [{"sku": "A1", "name": "Silla", "quantity": 2, "unit_price": "100.00", "line_total": "200.00"}]
        )
        self.assertEqual(order["total"], "200.00")
        self.assertIsNone(order["cancelled_at"])
        self.assertEqual(body["orders_not_created"][0]["error"], "SKU no encontrado en Shopify: X")
        self.assertIsNone(body["last_synced_at"])

    def test_web_order_uses_shopify_customer_and_pack_numbers(self, shopify):
        _local_order("20", tags=["Addi-Marketplace"])
        _local_order("30", tags=["mercadolibre"])
        for order_id in ("ML-2", "ML-1"):
            _row(order_id, marketplace=MarketplaceOrder.Marketplace.MERCADOLIBRE, shopify_order_id="30")

        orders = {order["shopify_order_id"]: order for order in self._get()["orders"]}

        self.assertEqual(orders["20"]["marketplace"], "shopify")
        self.assertEqual(orders["20"]["customer"]["identification"], "900")
        self.assertEqual(orders["20"]["customer"]["email"], "web@example.com")
        self.assertEqual(orders["20"]["fulfillment"]["status"], "")
        self.assertEqual(orders["30"]["marketplace_order_numbers"], ["ML-1", "ML-2"])

    def test_filters(self, shopify):
        from .functions.order_listing_format import COLOMBIA_TZ

        day = timezone.datetime(2026, 10, 3, 23, 30, tzinfo=COLOMBIA_TZ)
        _local_order("1", name="20131", tags=["sodimac"], created_at=day)
        _local_order("2", tags=["falabella"], created_at=day - timedelta(days=5))
        _local_order("3", created_at=day)
        _row("FA-9", number="3254", shopify_order_id="2")

        def ids(**params):
            return {order["shopify_order_id"] for order in self._get(params)["orders"]}

        self.assertEqual(ids(marketplace="sodimac"), {"1"})
        self.assertEqual(ids(marketplace="shopify"), {"3"})
        self.assertEqual(ids(date_from="2026-10-03", date_to="2026-10-03"), {"1", "3"})
        self.assertEqual(ids(search="#20131"), {"1"})
        self.assertEqual(ids(search="3254"), {"2"})

    def test_excludes_deleted_and_orders_newest_first(self, shopify):
        from .functions.mark_shopify_order_deleted import mark_shopify_order_deleted

        _local_order("1", created_at=timezone.now() - timedelta(days=1))
        _local_order("2", created_at=timezone.now())
        _local_order("3")
        mark_shopify_order_deleted("3")
        mark_shopify_order_deleted("99")

        body = self._get()

        self.assertEqual([order["shopify_order_id"] for order in body["orders"]], ["2", "1"])
        self.assertEqual(body["count"], 2)

    def test_page_number_pagination_and_last_synced_at(self, shopify):
        for index in range(5):
            _local_order(str(index))
        ShopifyOrderSyncState.objects.create(pk=1, last_reconciled_at=timezone.now())

        first = self._get({"page_size": 2})
        third = self._get({"page_size": 2, "page": 3})

        self.assertEqual(first["count"], 5)
        self.assertEqual(len(first["orders"]), 2)
        self.assertIn("page=2", first["next"])
        self.assertIsNone(first["previous"])
        self.assertEqual(len(third["orders"]), 1)
        self.assertIsNone(third["next"])
        self.assertIsNotNone(first["last_synced_at"])

    def test_queries_do_not_grow_with_orders(self, shopify):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries_for(count):
            ShopifyOrder.objects.all().delete()
            MarketplaceOrder.objects.all().delete()
            for index in range(count):
                _local_order(str(index), tags=["falabella"])
                _row(f"FA-{index}", shopify_order_id=str(index), status=MarketplaceOrder.Status.ERROR_ORDER)
            with CaptureQueriesContext(connection) as context:
                self.client.get(self.url)
            return len(context.captured_queries)

        self.client.force_login(self.admin)
        self.assertEqual(queries_for(2), queries_for(10))

    def test_rejects_without_session_or_role(self, shopify):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_invalid_params(self, shopify):
        self.client.force_login(self.admin)
        for params in (
            {"marketplace": "amazon"},
            {"date_from": "03/10/2026"},
            {"date_from": "2026-10-05", "date_to": "2026-10-01"},
            {"page_size": 0},
            {"page_size": 51},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.url, params).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"page": 99}).status_code, 404)
