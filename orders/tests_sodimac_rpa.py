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
# Rótulo Home Delivery: llega sin relación y la guía es el número de la OC.
HOME_DELIVERY = _pdf("Entrega: Home Delivery Guia: 16200704 Servicio")
Result = MarketplaceOrder.LabelRequestResult


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
        # Rótulo de Sodimac "Home Delivery": la guía es el número de la OC.
        self.assertEqual(read_label_tracking(_pdf("Entrega: Home Delivery Guia: 16207229 Servicio")), "16207229")
        self.assertEqual(read_label_tracking(_pdf("sin numero")), "")
        with self.assertLogs("pypdf", level="WARNING"):
            self.assertEqual(read_label_tracking(b"%PDF-1.4 roto"), "")


@patch(f"{R}.SODIMAC_RPA_URL", "https://rpa.example")
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

    def test_a_new_request_clears_the_last_result(self, trigger):
        _oc("1", label_request_status=Request.ERROR, label_request_note="x", label_request_result=Result.TIMEOUT)

        self._request()

        row = MarketplaceOrder.objects.get()
        self.assertEqual((row.label_request_status, row.label_request_result), (Request.REQUESTED, ""))

    def test_a_busy_rpa_marks_nothing_so_they_go_next_time(self, trigger):
        from integrations.sodimac_rpa.client import SodimacRpaBusy

        trigger.side_effect = SodimacRpaBusy("Ya hay una ejecución en curso")
        _oc("1")

        self.assertEqual(self._request(), [])
        self.assertEqual(MarketplaceOrder.objects.get().label_request_status, "")

    def test_nothing_pending_does_not_call_the_rpa(self, trigger):
        self.assertEqual(self._request(), [])
        trigger.assert_not_called()

    def test_without_rpa_url_the_sync_skips_it(self, trigger):
        _oc("1")
        with patch(f"{R}.SODIMAC_RPA_URL", ""):
            self.assertEqual(self._request(), [])
        trigger.assert_not_called()

    def test_chosen_ocs_go_even_in_manual_review_but_not_without_warehouse_or_with_label(self, trigger):
        from .functions.sodimac_rpa import request_sodimac_labels

        _oc("1", label_request_status=Request.MANUAL)
        _oc("2", label_request_status=Request.REQUESTED, label_requested_at=timezone.now())
        _oc("3", fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD)
        MarketplaceLabel.objects.create(order=_oc("4"), tracking_number="1", label_pdf=ROTULO, relation_pdf=RELACION)
        _oc("5")  # no elegida

        self.assertEqual(sorted(request_sodimac_labels(["1", "2", "3", "4"])), ["1", "2"])
        self.assertEqual(sorted(trigger.call_args.args[0]), ["1", "2"])


@patch(f"{R}.trigger_labels", return_value={"started": True})
class RequestLabelAdminActionTests(TestCase):
    def test_a_staff_user_asks_the_rpa_for_the_selected_ocs(self, trigger):
        _oc("1")
        _oc("2")
        user = User.objects.create_superuser("admin", password="x")
        self.client.force_login(user)
        ids = MarketplaceOrder.objects.filter(marketplace_order_id="1").values_list("pk", flat=True)

        response = self.client.post(
            "/admin/orders/marketplaceorder/",
            {"action": "request_rpa_label", "_selected_action": list(ids)},
            follow=True,
        )

        self.assertContains(response, "Pedidas al RPA: 1.")
        self.assertEqual(trigger.call_args.args[0], ["1"])


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
        self.assertEqual(self._processed(resultado="guia_generada").status_code, 200)

        label = MarketplaceLabel.objects.get(order=self.order)
        self.assertEqual((label.tracking_number, bytes(label.label_pdf), bytes(label.relation_pdf)), ("473402021", ROTULO, RELACION))
        self.assertEqual(
            (self._refresh().label_request_status, self.order.label_request_result), (Request.RECEIVED, Result.GENERATED)
        )

    def test_a_failed_order_is_asked_again_later(self):
        self._processed(guia_generada=False, mensaje="Timeout buscando la orden", resultado="tiempo_agotado")

        self.assertFalse(MarketplaceLabel.objects.exists())
        self.assertEqual((self._refresh().label_request_status, self.order.label_request_result), (Request.ERROR, Result.TIMEOUT))
        self.assertIn("Timeout", self.order.label_request_note)

    def test_a_browser_error_in_one_order_is_asked_again_later(self):
        self._processed(guia_generada=False, mensaje="ElementClickInterceptedException: ...", resultado="error_portal")

        self.assertEqual((self._refresh().label_request_status, self.order.label_request_result), (Request.ERROR, Result.PORTAL_ERROR))

    def test_already_generated_without_pdfs_goes_to_manual_review(self):
        self._processed(rotulo_pdf_base64=None, relacion_pdf_base64=None, mensaje="botón deshabilitado",
                        resultado="guia_ya_generada")

        self.assertEqual(self._refresh().label_request_status, Request.MANUAL)
        self.assertEqual(self.order.label_request_result, Result.ALREADY_GENERATED)
        self.assertIn("a mano", self.order.label_request_note)

    def test_a_home_delivery_label_is_saved_without_relation(self):
        self._processed(rotulo_pdf_base64=_b64(HOME_DELIVERY), relacion_pdf_base64=None, resultado="guia_un_pdf")

        label = MarketplaceLabel.objects.get(order=self.order)
        self.assertEqual((label.tracking_number, bytes(label.relation_pdf)), ("16200704", b""))
        self.assertEqual(
            (self._refresh().label_request_status, self.order.label_request_result), (Request.RECEIVED, Result.SINGLE_PDF)
        )

    def test_a_tcc_label_without_relation_goes_to_manual_review(self):
        self._processed(relacion_pdf_base64=None, resultado="guia_un_pdf")

        self.assertFalse(MarketplaceLabel.objects.exists())
        self.assertEqual(self._refresh().label_request_status, Request.MANUAL)
        self.assertIn("Falta la relación", self.order.label_request_note)
        self.assertEqual(self.order.label_request_result, Result.SINGLE_PDF)

    def test_an_unknown_or_missing_result_is_stored_empty(self):
        with self.assertLogs(R, level="WARNING"):
            self.assertEqual(self._processed(resultado="algo_nuevo").status_code, 200)
        self.assertEqual((self._refresh().label_request_status, self.order.label_request_result), (Request.RECEIVED, ""))

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

        self.assertEqual((self._refresh().label_request_status, self.order.label_request_result), (Request.ERROR, Result.LOGIN_FAILED))
        answered.refresh_from_db()
        self.assertEqual((answered.label_request_status, answered.label_request_result), (Request.REQUESTED, ""))

    def test_a_successful_batch_marks_the_unanswered_ocs_without_response(self):
        self._post({"evento": "proceso_finalizado", "proceso_exitoso": True, "ordenes_procesadas": []})

        self.assertEqual((self._refresh().label_request_status, self.order.label_request_result), (Request.ERROR, Result.NO_RESPONSE))

    def test_wrong_token_unknown_event_or_unknown_oc(self):
        self.assertEqual(self._post({"evento": "orden_procesada"}, token="").status_code, 403)
        self.assertEqual(self._post({"evento": "orden_procesada"}, token="otro").status_code, 403)
        self.assertEqual(self._post({"evento": "otro"}).status_code, 400)
        self.assertEqual(self._processed(numero_orden="999").status_code, 400)
        self.assertFalse(MarketplaceLabel.objects.exists())


class ChannelLabelInPanelTests(TestCase):
    """El panel muestra qué OC de Sodimac tienen guía y deja bajar los PDF."""

    def setUp(self):
        from .models import ShopifyOrder

        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        self.with_label = _oc("1", label_request_result=Result.GENERATED)
        MarketplaceLabel.objects.create(order=self.with_label, tracking_number="474272966", label_pdf=ROTULO, relation_pdf=RELACION)
        _oc("2", label_request_status=Request.MANUAL, label_request_note="Ya generada en el portal",
            label_request_result=Result.ALREADY_GENERATED)
        MarketplaceLabel.objects.create(order=_oc("3"), tracking_number="3", label_pdf=HOME_DELIVERY)
        for oc, name in (("1", "20482"), ("2", "20483"), ("3", "20485")):
            ShopifyOrder.objects.create(shopify_id=f"s{oc}", name=name, marketplace="sodimac")
        ShopifyOrder.objects.create(shopify_id="w1", name="20484", marketplace="shopify")

    def test_the_listing_says_which_have_a_label(self):
        rows = {row["shopify_order_name"]: row for row in self.client.get("/api/orders/").json()["orders"]}

        self.assertEqual(
            {k: rows["20482"]["channel_label"][k] for k in ("status", "tracking_number", "result", "has_relation")},
            {"status": "recibida", "tracking_number": "474272966", "result": "guia_generada", "has_relation": True},
        )
        self.assertEqual(
            {k: rows["20483"]["channel_label"][k] for k in ("status", "note", "result", "result_label")},
            {"status": "manual", "note": "Ya generada en el portal", "result": "guia_ya_generada",
             "result_label": "La guía ya estaba generada en el portal"},
        )
        self.assertFalse(rows["20485"]["channel_label"]["has_relation"])
        self.assertIsNone(rows["20484"]["channel_label"])  # tienda web: no aplica

    def test_a_label_without_relation_has_no_relation_to_download(self):
        self.assertEqual(self.client.get("/api/orders/s3/channel-label/rotulo/").status_code, 200)
        self.assertEqual(self.client.get("/api/orders/s3/channel-label/relacion/").status_code, 404)

    def test_downloads_the_label_and_the_relation(self):
        response = self.client.get("/api/orders/s1/channel-label/rotulo/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('filename="sodimac-1-rotulo.pdf"', response["Content-Disposition"])
        self.assertEqual(response.content, ROTULO)
        self.assertEqual(self.client.get("/api/orders/s1/channel-label/relacion/").content, RELACION)

    @override_settings(CORS_ALLOWED_ORIGINS=["https://front.example"])
    def test_the_frontend_can_read_the_file_name_across_domains(self):
        response = self.client.get("/api/orders/s1/channel-label/rotulo/", HTTP_ORIGIN="https://front.example")

        self.assertIn("content-disposition", response["Access-Control-Expose-Headers"].lower())

    def test_without_label_or_unknown_document_is_404(self):
        self.assertEqual(self.client.get("/api/orders/s2/channel-label/rotulo/").status_code, 404)
        self.assertEqual(self.client.get("/api/orders/s1/channel-label/otro/").status_code, 404)

    def test_users_without_the_role_cannot_download(self):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get("/api/orders/s1/channel-label/rotulo/").status_code, 403)


@patch(f"{R}.trigger_labels", return_value={"started": True})
class RequestLabelButtonTests(TestCase):
    """Botón "Generar guía (RPA)" en los pedidos de Sodimac."""

    URL = "/api/orders/{}/channel-label/request/"

    def setUp(self):
        from .models import ShopifyOrder

        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        self.row = _oc("16206245", label_request_status=Request.MANUAL, label_request_note="falló antes")
        ShopifyOrder.objects.create(shopify_id="s16206245", name="20482", marketplace="sodimac")

    def test_sends_the_oc_to_the_rpa_even_after_a_manual_review(self, trigger):
        response = self.client.post(self.URL.format("s16206245"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(trigger.call_args.args[0], ["16206245"])
        self.assertEqual(response.json()["channel_label"]["status"], "solicitada")

    def test_with_a_label_already_it_is_refused(self, trigger):
        MarketplaceLabel.objects.create(order=self.row, tracking_number="1", label_pdf=ROTULO, relation_pdf=RELACION)

        response = self.client.post(self.URL.format("s16206245"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("ya tiene guía", response.json()["detail"])
        trigger.assert_not_called()

    def test_an_order_split_in_two_warehouses_in_shopify_is_refused(self, trigger):
        from .models import ShopifyFulfillmentOrder, ShopifyOrder

        order = ShopifyOrder.objects.get(shopify_id="s16206245")
        for position, location_id in enumerate([ENVIA_LOCATION, "98784313621"]):
            ShopifyFulfillmentOrder.objects.create(
                order=order, position=position, shopify_id=f"fo{position}", status="OPEN", location_id=location_id
            )

        response = self.client.post(self.URL.format("s16206245"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("varias bodegas", response.json()["detail"])
        trigger.assert_not_called()

    def test_the_proveedores_location_is_not_valid(self, trigger):
        self.row.fulfillment_location_name = "Proveedores"
        self.row.save()

        response = self.client.post(self.URL.format("s16206245"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("no es válida", response.json()["detail"])
        trigger.assert_not_called()

    def test_busy_or_unreachable_rpa(self, trigger):
        from integrations.sodimac_rpa.client import SodimacRpaBusy, SodimacRpaError

        trigger.side_effect = SodimacRpaBusy("ocupado")
        self.assertEqual(self.client.post(self.URL.format("s16206245")).status_code, 409)
        trigger.side_effect = SodimacRpaError("SODIMAC_RPA_URL no está configurada.")
        response = self.client.post(self.URL.format("s16206245"))
        self.assertEqual(response.status_code, 502)
        self.assertIn("SODIMAC_RPA_URL", response.json()["detail"])

    def test_users_without_the_role_are_refused(self, trigger):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.post(self.URL.format("s16206245")).status_code, 403)
        trigger.assert_not_called()


class UploadLabelButtonTests(TestCase):
    """Botón "Cargar guía": una persona sube rótulo y relación."""

    URL = "/api/orders/s16206245/channel-label/upload/"

    def setUp(self):
        from .models import ShopifyOrder

        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        self.row = _oc("16206245", label_request_status=Request.MANUAL, label_request_note="ya generada en el portal")
        self.shopify = ShopifyOrder.objects.create(shopify_id="s16206245", name="20482", marketplace="sodimac")

    def _upload(self, label=ROTULO, relation=RELACION, **data):
        from django.core.files.uploadedfile import SimpleUploadedFile

        files = {"label": SimpleUploadedFile("rotulo.pdf", label, "application/pdf")}
        if relation is not None:
            files["relation"] = SimpleUploadedFile("relacion.pdf", relation, "application/pdf")
        return self.client.post(self.URL, {**files, **data})

    def test_uploads_both_pdfs_and_reads_the_number_from_the_label(self):
        MarketplaceOrder.objects.filter(pk=self.row.pk).update(label_request_result=Result.ALREADY_GENERATED)

        response = self._upload()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channel_label"]["tracking_number"], "473402021")
        label = MarketplaceLabel.objects.get(order=self.row)
        self.assertEqual((bytes(label.label_pdf), bytes(label.relation_pdf)), (ROTULO, RELACION))
        self.row.refresh_from_db()
        self.assertEqual(
            (self.row.label_request_status, self.row.label_request_note, self.row.label_request_result),
            (Request.RECEIVED, "", ""),
        )

    def test_a_home_delivery_label_is_uploaded_without_relation(self):
        response = self._upload(label=_pdf("Home Delivery Guia: 16206245"), relation=None)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["channel_label"]["has_relation"])
        self.assertEqual(MarketplaceLabel.objects.get().tracking_number, "16206245")
        self.assertIn("Falta la relación", self._upload(relation=None).json()["detail"])

    def test_a_typed_number_is_used_when_the_label_has_none(self):
        response = self._upload(label=_pdf("sin numero"), relation=_pdf("sin numero"), tracking_number="474000001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MarketplaceLabel.objects.get().tracking_number, "474000001")

    def test_invalid_files_mismatched_numbers_or_no_number_are_400(self):
        self.assertIn("no es un PDF", self._upload(label=b"hola").json()["detail"])
        self.assertIn("distintos", self._upload(relation=_pdf("Remesa: 473400000")).json()["detail"])
        self.assertIn("escribirlo a mano", self._upload(label=_pdf("x"), relation=_pdf("y")).json()["detail"])
        self.assertFalse(MarketplaceLabel.objects.exists())

    def test_once_the_warehouse_was_notified_it_is_not_replaced(self):
        Dispatch.objects.create(shopify_order=self.shopify, channel="sodimac", status=Dispatch.Status.NOTIFIED)

        response = self._upload()

        self.assertEqual(response.status_code, 400)
        self.assertIn("ya fue avisada", response.json()["detail"])

    def test_users_without_the_role_are_refused(self):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self._upload().status_code, 403)
        self.assertFalse(MarketplaceLabel.objects.exists())


@patch(f"{R}.SODIMAC_RPA_URL", "https://rpa.example/")
class RpaPanelTests(TestCase):
    """Recuadro "RPA de guías": estado, visor en vivo, Continuar y Detener."""

    def setUp(self):
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

    @patch(f"{R}.get_status", return_value={
        "state": "VALIDACION_HUMANA", "message": "Resolver CAPTCHA", "running": True,
        "pending_intervention": {"reason": "captcha detectado"},
    })
    def test_status_with_the_live_viewer_link(self, get_status):
        data = self.client.get("/api/orders/sodimac/rpa/status/").json()

        self.assertEqual(data["viewer_url"], "https://rpa.example/vnc/vnc.html?autoconnect=true&resize=scale")
        self.assertEqual((data["running"], data["state"], data["waiting_human"]), (True, "VALIDACION_HUMANA", True))
        self.assertEqual(data["waiting_reason"], "captcha detectado")

    @patch(f"{R}.get_status")
    def test_an_unreachable_or_unconfigured_rpa_is_reported_not_raised(self, get_status):
        from integrations.sodimac_rpa.client import SodimacRpaError

        get_status.side_effect = SodimacRpaError("No se pudo conectar con el RPA")
        data = self.client.get("/api/orders/sodimac/rpa/status/").json()
        self.assertEqual((data["reachable"], data["error"]), (False, "No se pudo conectar con el RPA"))
        with patch(f"{R}.SODIMAC_RPA_URL", ""):
            self.assertFalse(self.client.get("/api/orders/sodimac/rpa/status/").json()["configured"])

    @patch(f"{R}.get_status", return_value={"state": "AUTENTICADO", "running": True})
    @patch(f"{R}.kill_run", return_value={"killed": True})
    @patch(f"{R}.continue_run", return_value={"resumed": True})
    def test_continue_and_kill(self, continue_run, kill_run, get_status):
        self.assertEqual(self.client.post("/api/orders/sodimac/rpa/continue/").status_code, 200)
        self.assertEqual(self.client.post("/api/orders/sodimac/rpa/kill/").status_code, 200)
        continue_run.assert_called_once()
        kill_run.assert_called_once()

    @patch(f"{R}.kill_run")
    def test_kill_without_a_run_is_409(self, kill_run):
        from integrations.sodimac_rpa.client import SodimacRpaBusy

        kill_run.side_effect = SodimacRpaBusy("No hay ninguna ejecución en curso")
        response = self.client.post("/api/orders/sodimac/rpa/kill/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("ninguna corrida", response.json()["detail"])

    @patch(f"{R}.kill_run")
    def test_users_without_the_role_are_refused(self, kill_run):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get("/api/orders/sodimac/rpa/status/").status_code, 403)
        self.assertEqual(self.client.post("/api/orders/sodimac/rpa/kill/").status_code, 403)
        kill_run.assert_not_called()


@patch(f"{R}.get_status", return_value={"running": True})
@patch(f"{R}.trigger_labels", return_value={"started": True})
class BulkLabelsButtonTests(TestCase):
    """Botón "Generar guías disponibles": solo van al RPA las OC cuya bodega
    en la plataforma es la misma que tiene el pedido en Shopify."""

    URL = "/api/orders/sodimac/rpa/bulk-labels/"
    BARU = "98784313621"

    def setUp(self):
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

    def _order(self, oc, *locations, **fields):
        from .models import ShopifyFulfillmentOrder, ShopifyOrder

        order = ShopifyOrder.objects.create(shopify_id=f"s{oc}", name=f"#{oc}", marketplace="sodimac", **fields)
        for position, (location_id, name, status) in enumerate(locations):
            ShopifyFulfillmentOrder.objects.create(
                order=order, position=position, shopify_id=f"fo{oc}{position}", status=status,
                location_id=location_id, location_name=name,
            )
        return order

    def _scenario(self):
        _oc("1")
        self._order("1", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        _oc("2", label_request_status=Request.ERROR)
        # Un fulfillment order cerrado en otra bodega no cuenta.
        self._order("2", (self.BARU, "Baru", "CLOSED"), (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        _oc("3")
        self._order("3", (self.BARU, "Baru", "OPEN"))
        _oc("4")
        self._order("4", (ENVIA_LOCATION, "Bodega Envia", "OPEN"), (self.BARU, "Baru", "OPEN"))
        _oc("5")
        self._order("5")
        _oc("6", fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD, fulfillment_note="sin stock")
        self._order("6", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        _oc("7")
        _oc("8")
        self._order("8", (ENVIA_LOCATION, "Bodega Envia", "OPEN"), cancelled_at=timezone.now())
        # "Proveedores" no es bodega válida aunque coincida con Shopify.
        _oc("12", fulfillment_location_id="94018535701", fulfillment_location_name="Proveedores")
        self._order("12", ("94018535701", "Proveedores", "OPEN"))
        # Siguen a la vista hasta tener guía: la de revisión manual (el RPA no
        # pudo) disponible con su novedad; la pedida hace poco, como novedad.
        # Fuera: con guía.
        _oc("9", label_request_status=Request.REQUESTED, label_requested_at=timezone.now())
        self._order("9", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        _oc("10", label_request_status=Request.MANUAL, label_request_note="Guía ya generada en el portal.")
        self._order("10", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        MarketplaceLabel.objects.create(order=_oc("11"), tracking_number="1", label_pdf=ROTULO, relation_pdf=RELACION)

    def test_the_preview_splits_matching_and_mismatched_warehouses(self, trigger, *_):
        self._scenario()

        body = self.client.get(self.URL).json()

        self.assertEqual([item["purchase_order"] for item in body["ready"]], ["1", "2", "10"])
        self.assertIn("Guía ya generada en el portal", body["ready"][2]["note"])
        self.assertEqual(body["ready"][0], {
            "shopify_order_id": "s1", "order_name": "#1", "purchase_order": "1",
            "platform_location": "Bodega Envia", "shopify_location": "Bodega Envia", "reason": "", "note": "",
        })
        reasons = {item["purchase_order"]: item["reason"] for item in body["blocked"]}
        self.assertEqual(sorted(reasons, key=int), ["3", "4", "5", "6", "7", "8", "9", "12"])
        self.assertIn("distinta", reasons["3"])
        self.assertIn("varias bodegas", reasons["4"])
        self.assertIn("no tiene bodega abierta", reasons["5"])
        self.assertIn("sin stock", reasons["6"])
        self.assertIn("copia local", reasons["7"])
        self.assertIn("cancelado", reasons["8"])
        self.assertIn("Proveedores no es válida", reasons["12"])
        self.assertIn("esperando respuesta", reasons["9"])
        self.assertEqual(body["blocked"][0]["shopify_location"], "Baru")
        trigger.assert_not_called()

    def test_confirming_sends_only_the_matching_ocs_in_one_batch(self, trigger, *_):
        self._scenario()

        response = self.client.post(self.URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(trigger.call_args.args[0]), ["1", "10", "2"])
        self.assertEqual(sorted(response.json()["sent"]), ["1", "10", "2"])
        self.assertEqual(len(response.json()["blocked"]), 8)
        requested = MarketplaceOrder.objects.filter(label_request_status=Request.REQUESTED)
        self.assertEqual(sorted(requested.values_list("marketplace_order_id", flat=True)), ["1", "10", "2", "9"])

    def test_only_the_chosen_ocs_go_and_never_a_blocked_one(self, trigger, *_):
        self._scenario()

        response = self.client.post(self.URL, {"purchase_orders": ["2", "3"]}, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(trigger.call_args.args[0], ["2"])  # la 3 tiene novedad de bodega
        self.assertEqual(response.json()["sent"], ["2"])
        self.assertEqual(
            self.client.post(self.URL, {"purchase_orders": "2"}, content_type="application/json").status_code, 400
        )

    def test_nothing_available_or_a_busy_rpa(self, trigger, *_):
        _oc("3")
        self._order("3", (self.BARU, "Baru", "OPEN"))
        response = self.client.post(self.URL)
        self.assertEqual(response.status_code, 400)
        trigger.assert_not_called()

        from integrations.sodimac_rpa.client import SodimacRpaBusy

        _oc("1")
        self._order("1", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        trigger.side_effect = SodimacRpaBusy("ocupado")
        self.assertEqual(self.client.post(self.URL).status_code, 409)
        self.assertEqual(MarketplaceOrder.objects.get(marketplace_order_id="1").label_request_status, "")

    def test_a_request_the_rpa_left_unanswered_is_available_again(self, trigger, get_status):
        _oc("9", label_request_status=Request.REQUESTED, label_requested_at=timezone.now())
        self._order("9", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        # Ya despachado por otro lado: no aparece.
        _oc("13")
        self._order("13", (ENVIA_LOCATION, "Bodega Envia", "CLOSED"), fulfillment_status="FULFILLED")
        get_status.return_value = {"running": False}

        body = self.client.get(self.URL).json()

        self.assertEqual([item["purchase_order"] for item in body["ready"]], ["9"])
        self.assertIn("sin responder", body["ready"][0]["note"])
        self.assertEqual(body["blocked"], [])

    def test_ocs_before_the_cutoff_date_are_left_out(self, trigger, *_):
        from datetime import datetime

        from .functions.sodimac_rpa import BULK_LABELS_SINCE

        # Anteriores al corte: las gestionó operaciones por otro lado.
        _oc("20", marketplace_created_at=timezone.make_aware(datetime(2026, 10, 26)))
        self._order("20", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))
        _oc("21", marketplace_created_at=BULK_LABELS_SINCE)
        self._order("21", (ENVIA_LOCATION, "Bodega Envia", "OPEN"))

        body = self.client.get(self.URL).json()

        self.assertEqual([item["purchase_order"] for item in body["ready"]], ["21"])
        self.assertEqual(body["blocked"], [])
        self.client.post(self.URL, {"purchase_orders": ["20", "21"]}, content_type="application/json")
        self.assertEqual(trigger.call_args.args[0], ["21"])

    def test_users_without_the_role_are_refused(self, trigger, *_):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.post(self.URL).status_code, 403)
        trigger.assert_not_called()


@patch("orders.apis.SodimacRpaCallbackAPI.bearer_token", TOKEN)
@patch(f"{R}.trigger_labels", return_value={"started": True})
class RpaRunHistoryTests(TestCase):
    """Historial del RPA: cada envío es una corrida con sus OC, que el
    webhook va llenando y el fin del lote cierra."""

    URL = "/api/orders/sodimac/rpa/runs/"

    def setUp(self):
        self.user = User.objects.create_user("ops", email="ops@example.com", password="x")
        self.user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(self.user)
        _oc("16200704")
        _oc("16200705")

    def _callback(self, payload):
        return self.client.post(
            "/api/orders/sodimac/rpa/callback/", payload, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )

    def _send(self, *ocs):
        from .functions.sodimac_rpa import request_sodimac_labels
        from .models import SodimacRpaRun

        return request_sodimac_labels(list(ocs), source=SodimacRpaRun.Source.BULK, requested_by=self.user)

    def test_a_run_records_each_oc_and_closes_with_the_batch(self, trigger):
        self._send("16200704", "16200705")
        self._callback({
            "evento": "orden_procesada", "numero_orden": "16200704", "guia_generada": True,
            "resultado": "guia_generada", "rotulo_pdf_base64": _b64(ROTULO), "relacion_pdf_base64": _b64(RELACION),
        })
        self._callback({"evento": "proceso_finalizado", "proceso_exitoso": True, "ordenes_procesadas": ["16200704"]})

        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, 200)
        [run] = response.json()["runs"]
        self.assertEqual(
            (run["source"], run["requested_by"], run["status"]), ("lote", "ops@example.com", "terminada")
        )
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["summary"], {"total": 2, "solicitada": 0, "recibida": 1, "error": 1, "manual": 0})
        received, lost = run["items"]
        self.assertEqual(
            (received["purchase_order"], received["status"], received["result"], received["tracking_number"]),
            ("16200704", "recibida", "guia_generada", "473402021"),
        )
        self.assertEqual((lost["purchase_order"], lost["status"], lost["result"]), ("16200705", "error", "sin_respuesta"))

    def test_each_run_keeps_its_own_outcome(self, trigger):
        from .models import SodimacRpaRun

        self._send("16200704")
        self._callback({
            "evento": "orden_procesada", "numero_orden": "16200704", "guia_generada": False,
            "mensaje": "Timeout", "resultado": "tiempo_agotado",
        })
        # El RPA se cortó sin avisar el fin; la siguiente corrida cierra la anterior.
        self._send("16200704", "16200705")
        self._callback({"evento": "proceso_finalizado", "proceso_exitoso": False, "ordenes_procesadas": []})

        older, newer = SodimacRpaRun.objects.order_by("id")
        self.assertEqual(older.status, SodimacRpaRun.Status.INTERRUPTED)
        self.assertEqual(older.items.get().result, Result.TIMEOUT)
        self.assertEqual(newer.status, SodimacRpaRun.Status.LOGIN_FAILED)
        self.assertEqual(set(newer.items.values_list("result", flat=True)), {Result.LOGIN_FAILED})

    def test_a_busy_rpa_leaves_no_run(self, trigger):
        from integrations.sodimac_rpa.client import SodimacRpaBusy

        from .models import SodimacRpaRun

        trigger.side_effect = SodimacRpaBusy("ocupado")
        with self.assertRaises(SodimacRpaBusy):
            self._send("16200704")
        self.assertFalse(SodimacRpaRun.objects.exists())

    def test_the_order_button_is_recorded_with_who_asked(self, trigger):
        from .models import ShopifyOrder, SodimacRpaRun

        ShopifyOrder.objects.create(shopify_id="s16200704", name="20482", marketplace="sodimac")
        self.client.post("/api/orders/s16200704/channel-label/request/")

        run = SodimacRpaRun.objects.get()
        self.assertEqual((run.source, run.requested_by), (SodimacRpaRun.Source.ORDER, self.user))

    def test_the_history_needs_the_role(self, trigger):
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.client.logout()
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.URL, {"limit": 0}).status_code, 400)
