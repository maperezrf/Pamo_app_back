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
        self.assertEqual(kwargs["tags"], ["falabella"])
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
class ProcessMercadoLibreOrderTests(TestCase):
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
        self.assertEqual(kwargs["tags"], ["mercadolibre"])
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
class ProcessMadecentroOrderTests(TestCase):
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
        self.assertEqual(kwargs["tags"], ["madecentro"])
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
