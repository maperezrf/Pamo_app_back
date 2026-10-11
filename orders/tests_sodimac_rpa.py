import base64
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import Dispatch, MarketplaceLabel, MarketplaceOrder, MarketplaceOrderItem
from .tests_dispatch import EMAIL_SETTINGS, ENVIA_LOCATION

R = "orders.functions.sodimac_rpa"
TOKEN = "rpa-token"
Request = MarketplaceOrder.LabelRequestStatus


def _pdf(text):
    """PDF mínimo con una línea de texto (el rótulo real trae "REMESA <n>")."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


ROTULO = _pdf("REMESA 473402021 Pieza 1 de 1")
RELACION = _pdf("Remesa: 473402021 Relacion de despacho")


def _b64(content):
    return base64.b64encode(content).decode()


def _oc(oc="16200704", **overrides):
    fields = dict(
        marketplace=MarketplaceOrder.Marketplace.SODIMAC, marketplace_order_id=oc, marketplace_order_number=oc,
        marketplace_status="1-PENDIENTE", status=MarketplaceOrder.Status.CREATED, shopify_order_id=f"s{oc}",
        fulfillment_status=MarketplaceOrder.FulfillmentStatus.ASSIGNED, fulfillment_location_id=ENVIA_LOCATION,
        fulfillment_location_name="Bodega Envia",
    )
    fields.update(overrides)
    order = MarketplaceOrder.objects.create(**fields)
    MarketplaceOrderItem.objects.create(order=order, marketplace_sku="PAMO_P04", quantity=1, unit_price="10")
    return order


class ReadLabelTrackingTests(SimpleTestCase):
    def test_reads_the_remesa_number_from_the_label(self):
        from .functions.read_label_tracking import read_label_tracking

        self.assertEqual(read_label_tracking(ROTULO), "473402021")
        self.assertEqual(read_label_tracking(_pdf("sin numero")), "")
        with self.assertLogs("pypdf", level="WARNING"):
            self.assertEqual(read_label_tracking(b"%PDF-1.4 roto"), "")


@patch(f"{R}.trigger_labels", return_value={"started": True})
class RequestSodimacLabelsTests(TestCase):
    def _request(self):
        from .functions.sodimac_rpa import request_sodimac_labels

        return request_sodimac_labels()

    def test_asks_for_the_open_ocs_without_label_and_marks_them(self, trigger):
        _oc("1")
        _oc("2", label_request_status=Request.ERROR)
        _oc("3", label_request_status=Request.REQUESTED, label_requested_at=timezone.now() - timedelta(hours=7))
        # Fuera: ya pedida hace poco, revisión manual, sin bodega, cerrada, con guía, sin Shopify.
        _oc("4", label_request_status=Request.REQUESTED, label_requested_at=timezone.now())
        _oc("5", label_request_status=Request.MANUAL)
        _oc("6", fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD)
        _oc("7", marketplace_status="4-ESTADO FINAL")
        MarketplaceLabel.objects.create(order=_oc("8"), tracking_number="1", label_pdf=ROTULO, relation_pdf=RELACION)
        _oc("9", shopify_order_id="", status=MarketplaceOrder.Status.PENDING)

        self.assertEqual(sorted(self._request()), ["1", "2", "3"])

        self.assertEqual(sorted(trigger.call_args.args[0]), ["1", "2", "3"])
        requested = MarketplaceOrder.objects.filter(label_request_status=Request.REQUESTED)
        self.assertEqual(sorted(requested.values_list("marketplace_order_id", flat=True)), ["1", "2", "3", "4"])

    def test_a_busy_rpa_marks_nothing_so_they_go_next_time(self, trigger):
        from integrations.sodimac_rpa.client import SodimacRpaBusy

        trigger.side_effect = SodimacRpaBusy("Ya hay una ejecución en curso")
        _oc("1")

        self.assertEqual(self._request(), [])
        self.assertEqual(MarketplaceOrder.objects.get().label_request_status, "")

    def test_nothing_pending_does_not_call_the_rpa(self, trigger):
        self.assertEqual(self._request(), [])
        trigger.assert_not_called()


@patch("orders.apis.SodimacRpaStockDispatchAPI.bearer_token", TOKEN)
class StockDispatchAPITests(TestCase):
    URL = "/api/orders/sodimac/rpa/orders/{}/stock-dispatch/"

    def test_the_rpa_gets_the_warehouse_with_its_bot_token(self):
        _oc()

        response = self.client.get(self.URL.format("16200704"), HTTP_X_BOT_TOKEN=TOKEN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["bodega_despacho"], "Bodega Envia")
        self.assertEqual(response.json()["productos"], [{"sku": "PAMO_P04", "cantidad": 1}])

    def test_without_warehouse_or_unknown_oc(self):
        _oc(fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD, fulfillment_note="sin stock")
        self.assertEqual(self.client.get(self.URL.format("16200704"), HTTP_X_BOT_TOKEN=TOKEN).status_code, 409)
        self.assertEqual(self.client.get(self.URL.format("999"), HTTP_X_BOT_TOKEN=TOKEN).status_code, 404)

    def test_wrong_token_or_a_user_session_is_refused(self):
        _oc()
        self.assertEqual(self.client.get(self.URL.format("16200704"), HTTP_X_BOT_TOKEN="otro").status_code, 403)
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.URL.format("16200704")).status_code, 403)


@patch("orders.apis.SodimacRpaCallbackAPI.bearer_token", TOKEN)
class RpaCallbackAPITests(TestCase):
    URL = "/api/orders/sodimac/rpa/callback/"

    def setUp(self):
        self.order = _oc(label_request_status=Request.REQUESTED, label_requested_at=timezone.now())

    def _post(self, payload, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(self.URL, payload, content_type="application/json", **headers)

    def _processed(self, **overrides):
        payload = {
            "evento": "orden_procesada", "numero_orden": "16200704", "guia_generada": True, "numero_guia": None,
            "mensaje": None, "rotulo_pdf_base64": _b64(ROTULO), "relacion_pdf_base64": _b64(RELACION),
        }
        payload.update(overrides)
        return self._post(payload)

    def _refresh(self):
        self.order.refresh_from_db()
        return self.order

    def test_a_processed_order_saves_both_pdfs_with_the_number_read_from_the_label(self):
        self.assertEqual(self._processed().status_code, 200)

        label = MarketplaceLabel.objects.get(order=self.order)
        self.assertEqual((label.tracking_number, bytes(label.label_pdf), bytes(label.relation_pdf)), ("473402021", ROTULO, RELACION))
        self.assertEqual(self._refresh().label_request_status, Request.RECEIVED)

    def test_a_failed_order_is_asked_again_later(self):
        self._processed(guia_generada=False, mensaje="Timeout buscando la orden")

        self.assertFalse(MarketplaceLabel.objects.exists())
        self.assertEqual(self._refresh().label_request_status, Request.ERROR)
        self.assertIn("Timeout", self.order.label_request_note)

    def test_already_generated_without_pdfs_goes_to_manual_review(self):
        self._processed(rotulo_pdf_base64=None, relacion_pdf_base64=None, mensaje="botón deshabilitado")

        self.assertEqual(self._refresh().label_request_status, Request.MANUAL)
        self.assertIn("a mano", self.order.label_request_note)

    def test_label_and_relation_with_different_numbers_go_to_manual_review(self):
        self._processed(relacion_pdf_base64=_b64(_pdf("Remesa: 473400000")))

        self.assertFalse(MarketplaceLabel.objects.exists())
        self.assertIn("distintos", self._refresh().label_request_note)

    def test_once_the_warehouse_was_notified_the_label_is_not_replaced(self):
        from .models import ShopifyOrder

        shopify = ShopifyOrder.objects.create(shopify_id="s16200704", name="20500", marketplace="sodimac")
        Dispatch.objects.create(shopify_order=shopify, channel="sodimac", status=Dispatch.Status.NOTIFIED)

        self._processed()

        self.assertFalse(MarketplaceLabel.objects.exists())
        self.assertEqual(self._refresh().label_request_status, Request.MANUAL)

    @override_settings(**EMAIL_SETTINGS)
    @patch(f"{R}.SODIMAC_RPA_ALERT_EMAILS", ["ops@example.com"])
    def test_a_captcha_emails_the_viewer_link(self):
        response = self._post({"evento": "validacion_humana", "state": "VALIDACION_HUMANA", "url": "x",
                               "reason": "captcha", "viewer_url": "https://rpa.example/vnc/vnc.html"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])
        self.assertIn("https://rpa.example/vnc/vnc.html", mail.outbox[0].body)

    def test_at_the_end_of_the_batch_unanswered_ocs_go_to_error(self):
        answered = _oc("1", label_request_status=Request.REQUESTED)

        self._post({"evento": "proceso_finalizado", "proceso_exitoso": False, "ordenes_procesadas": ["1"]})

        self.assertEqual(self._refresh().label_request_status, Request.ERROR)
        answered.refresh_from_db()
        self.assertEqual(answered.label_request_status, Request.REQUESTED)

    def test_wrong_token_unknown_event_or_unknown_oc(self):
        self.assertEqual(self._post({"evento": "orden_procesada"}, token="").status_code, 403)
        self.assertEqual(self._post({"evento": "orden_procesada"}, token="otro").status_code, 403)
        self.assertEqual(self._post({"evento": "otro"}).status_code, 400)
        self.assertEqual(self._processed(numero_orden="999").status_code, 400)
        self.assertFalse(MarketplaceLabel.objects.exists())
