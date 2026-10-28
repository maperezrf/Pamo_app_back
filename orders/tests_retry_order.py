from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase

from .models import MarketplaceOrder, MarketplaceOrderItem

RETRY = "orders.functions.retry_marketplace_order"
SODI = "orders.functions.sync_sodimac_orders"
Status = MarketplaceOrder.Status


def _oc(oc="16269493", marketplace=MarketplaceOrder.Marketplace.SODIMAC, **fields):
    values = dict(
        marketplace=marketplace, marketplace_order_id=oc, marketplace_order_number=oc, status=Status.ERROR_ORDER,
        error_description="SKU no encontrado en Shopify: 3119475",
    )
    values.update(fields)
    row = MarketplaceOrder.objects.create(**values)
    MarketplaceOrderItem.objects.create(order=row, marketplace_sku="3119475", quantity=2, unit_price="10")
    return row


def _created(orders, customer_id, financial_status):
    MarketplaceOrder.objects.filter(pk=orders[0].pk).update(
        status=Status.CREATED, error_description="", shopify_order_id="777", shopify_order_name="#1001"
    )


def _still_failing(orders, customer_id, financial_status):
    MarketplaceOrder.objects.filter(pk=orders[0].pk).update(
        status=Status.ERROR_ORDER, error_description="SKU no encontrado en Shopify: 3119475"
    )


@patch(f"{RETRY}.SODIMAC_SHOPIFY_CUSTOMER_ID", "7247084421397")
class RetryNotCreatedOrderTests(TestCase):
    """Botón "Reintentar" de un pedido no creado: tras corregir la
    equivalencia del SKU, crea la OC de Sodimac en Shopify al instante."""

    URL = "/api/orders/not-created/{}/retry/"

    def setUp(self):
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

    @patch(f"{SODI}.process_shipment", side_effect=_created)
    def test_creates_the_order_in_shopify(self, process_shipment):
        row = _oc()

        response = self.client.post(self.URL.format(row.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "created": True,
            "order": {"id": row.pk, "status": "orden_creada", "error": "", "shopify_order_id": "777",
                      "shopify_order_name": "#1001"},
        })
        self.assertEqual(process_shipment.call_args.args[1], "7247084421397")
        self.assertEqual(process_shipment.call_args.kwargs, {"financial_status": "PENDING"})

    @patch(f"{SODI}.process_shipment", side_effect=_still_failing)
    def test_a_sku_still_without_equivalence_comes_back_with_the_reason(self, process_shipment):
        row = _oc()

        body = self.client.post(self.URL.format(row.pk)).json()

        self.assertFalse(body["created"])
        self.assertEqual(body["order"]["status"], "error_creando_orden")
        self.assertIn("3119475", body["order"]["error"])

    @patch(f"{SODI}.process_shipment", side_effect=ConnectionError("Shopify caído"))
    def test_a_shopify_failure_leaves_it_for_manual_review(self, process_shipment):
        row = _oc()

        response = self.client.post(self.URL.format(row.pk))

        self.assertEqual(response.status_code, 502)
        row.refresh_from_db()
        self.assertEqual(row.status, Status.PROCESSING)

    @patch(f"{SODI}.process_shipment")
    def test_what_does_not_apply_is_refused_without_calling_shopify(self, process_shipment):
        processing = _oc("1", status=Status.PROCESSING)
        created = _oc("2", status=Status.CREATED, shopify_order_id="777")
        falabella = _oc("3", marketplace=MarketplaceOrder.Marketplace.FALABELLA)

        self.assertEqual(self.client.post(self.URL.format(processing.pk)).status_code, 409)
        self.assertEqual(self.client.post(self.URL.format(created.pk)).status_code, 400)
        self.assertIn("Sodimac", self.client.post(self.URL.format(falabella.pk)).json()["detail"])
        self.assertEqual(self.client.post(self.URL.format(999999)).status_code, 404)
        process_shipment.assert_not_called()

    @patch(f"{SODI}.process_shipment")
    def test_it_needs_the_role(self, process_shipment):
        row = _oc()
        self.client.force_login(User.objects.create_user("otro", password="x"))

        self.assertEqual(self.client.post(self.URL.format(row.pk)).status_code, 403)
        self.client.logout()
        self.assertIn(self.client.post(self.URL.format(row.pk)).status_code, (401, 403))
        process_shipment.assert_not_called()
