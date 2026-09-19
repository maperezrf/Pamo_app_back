from unittest.mock import patch

from django.test import TestCase

from customers.models import ShopifyCustomer
from integrations.shopify.functions.create_order import ShopifyOrderCreationError

from .functions.fetch_falabella_orders import fetch_falabella_orders
from .functions.import_falabella_orders import import_falabella_orders
from .functions.process_pending_orders import process_pending_orders
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
    @patch("orders.functions.process_pending_orders.get_variant_by_sku")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_creates_the_order_when_the_customer_already_exists_locally(
        self, mock_find, mock_variant, mock_create_order
    ):
        order = self._make_order()
        mock_find.return_value = ShopifyCustomer.objects.create(shopify_id="999", identification="123")
        mock_variant.return_value = "555"
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)
        self.assertEqual(order.shopify_order_id, "777")
        self.assertEqual(order.shopify_order_name, "#1001")
        self.assertEqual(order.shopify_customer_id, "999")
        _, kwargs = mock_create_order.call_args
        self.assertEqual(kwargs["items"], [{"variant_id": "555", "quantity": 2, "price": "100.00"}])
        self.assertEqual(kwargs["tags"], ["falabella"])
        self.assertIn("N-1", kwargs["note"])

    @patch("orders.functions.process_pending_orders.upsert_from_shopify_customer")
    @patch("orders.functions.process_pending_orders.create_customer_address")
    @patch("orders.functions.process_pending_orders.create_customer")
    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_by_sku")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_creates_a_new_shopify_customer_when_not_found_locally(
        self, mock_find, mock_variant, mock_create_order, mock_create_customer, mock_create_address, mock_upsert
    ):
        order = self._make_order()
        mock_find.return_value = None
        mock_create_customer.return_value = "999"
        mock_create_address.return_value = "gid://shopify/MailingAddress/1"
        mock_upsert.return_value = ShopifyCustomer(shopify_id="999", identification="123")
        mock_variant.return_value = "555"
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders()

        mock_create_customer.assert_called_once_with(email="ana@example.com", first_name="Ana", last_name="Gomez")
        mock_create_address.assert_called_once_with(
            customer_id="999", company="123", first_name="Ana", last_name="Gomez"
        )
        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.CREATED)

    @patch("orders.functions.process_pending_orders.create_customer")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_marks_error_creando_cliente_when_customer_data_is_insufficient(self, mock_find, mock_create_customer):
        order = self._make_order(customer_email="")
        mock_find.return_value = None
        mock_create_customer.side_effect = ValueError("se necesita al menos email o phone")

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_CUSTOMER)
        self.assertIn("email", order.error_description)

    @patch("orders.functions.process_pending_orders.get_variant_by_sku")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_marks_error_creando_orden_when_a_sku_does_not_resolve(self, mock_find, mock_variant):
        order = self._make_order()
        mock_find.return_value = ShopifyCustomer.objects.create(shopify_id="999", identification="123")
        mock_variant.return_value = None

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)
        self.assertIn("SKU-1", order.error_description)

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_by_sku")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_marks_error_creando_orden_when_shopify_rejects_the_order(
        self, mock_find, mock_variant, mock_create_order
    ):
        order = self._make_order()
        mock_find.return_value = ShopifyCustomer.objects.create(shopify_id="999", identification="123")
        mock_variant.return_value = "555"
        mock_create_order.side_effect = ShopifyOrderCreationError(["boom"])

        process_pending_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrder.Status.ERROR_ORDER)

    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_does_not_reprocess_an_order_that_already_has_a_shopify_order_id(self, mock_find):
        self._make_order(shopify_order_id="already-created")

        process_pending_orders()

        mock_find.assert_not_called()

    @patch("orders.functions.process_pending_orders.create_order")
    @patch("orders.functions.process_pending_orders.get_variant_by_sku")
    @patch("orders.functions.process_pending_orders.find_by_identification")
    def test_limit_caps_how_many_pending_orders_are_processed(self, mock_find, mock_variant, mock_create_order):
        self._make_order(marketplace_order_id="1")
        self._make_order(marketplace_order_id="2")
        mock_find.return_value = ShopifyCustomer.objects.create(shopify_id="999", identification="123")
        mock_variant.return_value = "555"
        mock_create_order.return_value = {"order_id": "777", "order_name": "#1001"}

        process_pending_orders(limit=1)

        self.assertEqual(
            MarketplaceOrder.objects.filter(status=MarketplaceOrder.Status.CREATED).count(), 1
        )
        self.assertEqual(
            MarketplaceOrder.objects.filter(status=MarketplaceOrder.Status.PENDING).count(), 1
        )


class ImportFalabellaOrdersTests(TestCase):
    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    @patch("orders.functions.import_falabella_orders.reconcile_from_shopify")
    def test_calls_the_three_stages_in_order_and_reports_completion(self, mock_reconcile, mock_fetch, mock_process):
        calls = []

        import_falabella_orders(progress_callback=lambda percent, step=None: calls.append(percent))

        mock_reconcile.assert_called_once()
        mock_fetch.assert_called_once()
        mock_process.assert_called_once()
        self.assertEqual(calls[-1], 100)

    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    @patch("orders.functions.import_falabella_orders.reconcile_from_shopify")
    def test_forwards_limit_from_params_to_process_pending_orders(self, mock_reconcile, mock_fetch, mock_process):
        import_falabella_orders(params={"limit": 1})

        _, kwargs = mock_process.call_args
        self.assertEqual(kwargs["limit"], 1)

    @patch("orders.functions.import_falabella_orders.process_pending_orders")
    @patch("orders.functions.import_falabella_orders.fetch_falabella_orders")
    @patch("orders.functions.import_falabella_orders.reconcile_from_shopify")
    def test_forwards_created_after_from_params_to_fetch_falabella_orders(
        self, mock_reconcile, mock_fetch, mock_process
    ):
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
