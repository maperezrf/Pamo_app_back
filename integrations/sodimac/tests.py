from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from .functions.get_orders import SodimacReportError, get_orders
from .functions.reinject_order import SodimacReinjectionError, reinject_order


class GetOrdersTests(SimpleTestCase):
    def test_rejects_an_unsupported_order_type(self):
        with self.assertRaises(ValueError):
            get_orders("9")

    @patch("integrations.sodimac.client.requests.post")
    def test_returns_an_empty_list_when_there_is_no_data(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Mensaje": "No hay datos para esta sentencia."}
        self.assertEqual(get_orders("1"), [])

    @patch("integrations.sodimac.client.requests.post")
    def test_raises_on_an_unexpected_message_instead_of_returning_silently(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Mensaje": "Algo distinto no visto antes."}
        with self.assertRaises(SodimacReportError):
            get_orders("1")

    @patch("integrations.sodimac.client.requests.post")
    def test_flattens_orders_into_one_row_per_product_with_vat_added(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {
            "Mensaje": "Sentencia ejecutada con éxito.",
            "Value": [
                {
                    "ORDEN_COMPRA": "OC-1",
                    "ESTADO_OC": "1-Pendiente",
                    "FECHA_TRANSMISION": "2026-09-01",
                    "PRODUCTOS": [
                        {"SKU": " ABC-1 ", "CANTIDAD_SKU": 2, "COSTO_SKU": 1000},
                        {"SKU": "XYZ-2", "CANTIDAD_SKU": 1, "COSTO_SKU": 500},
                    ],
                }
            ],
        }
        result = get_orders("1")
        self.assertEqual(
            result,
            [
                {
                    "purchase_order": "OC-1",
                    "status": "1-Pendiente",
                    "transmitted_at": "2026-09-01",
                    "sku": "ABC-1",
                    "quantity": 2,
                    "cost": Decimal("1000"),
                    "cost_with_vat": Decimal("1190.00"),
                },
                {
                    "purchase_order": "OC-1",
                    "status": "1-Pendiente",
                    "transmitted_at": "2026-09-01",
                    "sku": "XYZ-2",
                    "quantity": 1,
                    "cost": Decimal("500"),
                    "cost_with_vat": Decimal("595.00"),
                },
            ],
        )

    @patch("integrations.sodimac.client.requests.post")
    def test_sends_the_provider_reference_and_order_type(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Mensaje": "No hay datos para esta sentencia."}
        get_orders("4")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["TipoOrden"], "4")
        self.assertIn("ReferenciaProveedor", kwargs["json"])


class ReinjectOrderTests(SimpleTestCase):
    @patch("integrations.sodimac.client.requests.post")
    def test_returns_success_when_sodimac_confirms_it(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Value": [{"DESCRIPCION": "TRANSACCION EXITOSA"}]}
        self.assertEqual(reinject_order("OC-1"), {"purchase_order": "OC-1", "reinjected": True})

    @patch("integrations.sodimac.client.requests.post")
    def test_raises_when_sodimac_does_not_confirm_success(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Value": [{"DESCRIPCION": "ERROR"}]}
        with self.assertRaises(SodimacReinjectionError):
            reinject_order("OC-1")

    @patch("integrations.sodimac.client.requests.post")
    def test_raises_when_the_response_has_no_results(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Value": []}
        with self.assertRaises(SodimacReinjectionError):
            reinject_order("OC-1")

    @patch("integrations.sodimac.client.requests.post")
    def test_sends_the_purchase_order_as_pmg_po_number(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"Value": [{"DESCRIPCION": "TRANSACCION EXITOSA"}]}
        reinject_order("OC-42")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["PMG_PO_NUMBER"], "OC-42")
