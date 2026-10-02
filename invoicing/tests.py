from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from integrations.siigo.client import SiigoAPIError
from orders.models import MarketplaceOrder, MarketplaceOrderItem
from products.models import KitComponent, MarketplaceSku, Product

from .functions.build_sodimac_invoice import build_sodimac_invoice
from .functions.invoice_sodimac_orders import invoice_sodimac_orders
from .models import Invoice

CREATE_INVOICE = "invoicing.functions.invoice_sodimac_orders.create_invoice"
FINAL = "4-ESTADO FINAL"
SIIGO_OK = {"invoice_id": "abc-123", "raw": {"id": "abc-123", "name": "FV-1-1234", "total": 2250.92}}


def _order(oc="9001", marketplace_status=FINAL, items=(("54654", 2, "1000"),), **overrides):
    order = MarketplaceOrder.objects.create(
        marketplace=overrides.pop("marketplace", MarketplaceOrder.Marketplace.SODIMAC),
        marketplace_order_id=oc,
        marketplace_order_number=oc,
        marketplace_status=marketplace_status,
        **overrides,
    )
    for sku, quantity, price in items:
        MarketplaceOrderItem.objects.create(order=order, marketplace_sku=sku, quantity=quantity, unit_price=price)
    return order


def _siigo_rejection(message):
    return SiigoAPIError(400, {"Errors": [{"Code": "invalid", "Message": message}]})


class BuildSodimacInvoiceTests(TestCase):
    def setUp(self):
        MarketplaceSku.objects.create(product=Product.objects.create(sku="PAC1"), marketplace="sodimac", sku="54654")

    def test_simple_product_uses_the_pamo_sku_and_pamo_web_totals(self):
        built = build_sodimac_invoice(_order())

        self.assertEqual(
            built["items"],
            [{"code": "PAC1", "quantity": 2, "price": 1000.0, "discount": 0, "taxes": [{"id": 16104}, {"id": 13456}]}],
        )
        # subtotal 2000; iva 380; reteiva 57; reteica 22.08; retefuente 50.
        self.assertEqual(
            {key: built[key] for key in ("subtotal", "iva", "reteiva", "reteica", "retefuente", "total")},
            {
                "subtotal": Decimal("2000.00"),
                "iva": Decimal("380.00"),
                "reteiva": Decimal("57.00"),
                "reteica": Decimal("22.08"),
                "retefuente": Decimal("50.00"),
                "total": Decimal("2250.92"),
            },
        )

    def test_kit_goes_as_components_with_the_price_split(self):
        kit = Product.objects.create(sku="K1", is_kit=True)
        KitComponent.objects.create(kit=kit, component=Product.objects.create(sku="A"), quantity=1)
        KitComponent.objects.create(kit=kit, component=Product.objects.create(sku="B"), quantity=2)
        MarketplaceSku.objects.create(product=kit, marketplace="sodimac", sku="KIT-SODI")

        built = build_sodimac_invoice(_order(items=(("KIT-SODI", 1, "300"),)))

        self.assertEqual(
            [(item["code"], item["quantity"], item["price"]) for item in built["items"]], [("A", 1, 100.0), ("B", 2, 100.0)]
        )
        self.assertEqual(built["subtotal"], Decimal("300.00"))

    def test_sku_without_equivalence_goes_as_is(self):
        built = build_sodimac_invoice(_order(items=(("999", 1, "10.555"),)))

        self.assertEqual((built["items"][0]["code"], built["items"][0]["price"]), ("999", 10.56))

    def test_calculated_total_from_siigo_replaces_the_total(self):
        built = build_sodimac_invoice(_order(), calculated_total=Decimal("2250.91"))

        self.assertEqual(built["total"], Decimal("2250.91"))


@patch(CREATE_INVOICE, return_value=SIIGO_OK)
class InvoiceSodimacOrdersTests(TestCase):
    def setUp(self):
        MarketplaceSku.objects.create(product=Product.objects.create(sku="PAC1"), marketplace="sodimac", sku="54654")

    def test_final_oc_is_invoiced_without_stamping(self, create_invoice):
        order = _order()

        invoice_sodimac_orders()

        invoice = Invoice.objects.get(marketplace_order=order)
        self.assertEqual(invoice.status, Invoice.Status.CREATED)
        self.assertEqual((invoice.siigo_id, invoice.number, invoice.siigo_total), ("abc-123", "FV-1-1234", Decimal("2250.92")))
        self.assertEqual(invoice.total, Decimal("2250.92"))
        self.assertFalse(invoice.stamped)
        _, kwargs = create_invoice.call_args
        self.assertEqual(kwargs["document_id"], 26647)
        self.assertEqual(kwargs["customer"]["identification"], "800242106")
        self.assertEqual(kwargs["retention_ids"], [13457, 13464])
        self.assertEqual((kwargs["reteica"], kwargs["payment_value"], kwargs["payment_id"]), (22.08, 2250.92, 6507))
        self.assertEqual((kwargs["cost_center"], kwargs["seller"]), (116, 643))
        self.assertEqual(kwargs["purchase_order_number"], "9001")
        self.assertFalse(kwargs["stamp_send"])
        self.assertFalse(kwargs["mail_send"])
        self.assertEqual(invoice.request_payload["items"], kwargs["items"])

    def test_is_invoiced_even_if_the_shopify_order_failed(self, create_invoice):
        _order(status=MarketplaceOrder.Status.ERROR_ORDER)

        invoice_sodimac_orders()

        self.assertEqual(Invoice.objects.get().status, Invoice.Status.CREATED)

    def test_skips_open_ocs_other_marketplaces_and_already_invoiced(self, create_invoice):
        _order("1", marketplace_status="3-EN TRANSPORTE")
        _order("2", marketplace=MarketplaceOrder.Marketplace.MADECENTRO)
        invoice_sodimac_orders()
        create_invoice.assert_not_called()

        _order("3")
        invoice_sodimac_orders()
        invoice_sodimac_orders()

        create_invoice.assert_called_once()
        self.assertEqual(Invoice.objects.count(), 1)

    def test_rejection_is_marked_error_and_retried_next_run(self, create_invoice):
        order = _order()
        create_invoice.side_effect = [_siigo_rejection("sku no valido"), SIIGO_OK]

        with self.assertRaises(RuntimeError) as raised:
            invoice_sodimac_orders()

        invoice = Invoice.objects.get(marketplace_order=order)
        self.assertEqual((invoice.status, invoice.error_message), (Invoice.Status.ERROR, "sku no valido"))
        self.assertIn("9001: sku no valido", str(raised.exception))

        invoice_sodimac_orders()

        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.error_message), (Invoice.Status.CREATED, ""))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_rejection_with_calculated_total_is_retried_with_that_total(self, create_invoice):
        _order()
        create_invoice.side_effect = [
            _siigo_rejection(
                "The total payments must be equal to the total invoice. The total invoice calculated is 2250.91"
            ),
            SIIGO_OK,
        ]

        with self.assertRaises(RuntimeError):
            invoice_sodimac_orders()
        self.assertEqual(Invoice.objects.get().siigo_calculated_total, Decimal("2250.91"))

        invoice_sodimac_orders()

        self.assertEqual(create_invoice.call_args_list[1].kwargs["payment_value"], 2250.91)
        self.assertEqual(Invoice.objects.get().total, Decimal("2250.91"))

    def test_uncertain_failure_stays_creating_and_is_not_retried(self, create_invoice):
        _order()
        create_invoice.side_effect = ConnectionError("timeout")

        with self.assertRaises(RuntimeError) as raised:
            invoice_sodimac_orders()

        invoice = Invoice.objects.get()
        self.assertEqual(invoice.status, Invoice.Status.CREATING)
        self.assertIn("timeout", invoice.error_message)
        self.assertIn("revisar en Siigo", str(raised.exception))

        invoice_sodimac_orders()
        create_invoice.assert_called_once()

    def test_limit_caps_the_run(self, create_invoice):
        _order("1")
        _order("2")

        invoice_sodimac_orders({"limit": 1})

        create_invoice.assert_called_once()

    def test_process_type_is_seeded(self, create_invoice):
        from orchestrator.models import ProcessType

        self.assertFalse(ProcessType.objects.get(code="invoicing.invoice_sodimac").allow_concurrent)
