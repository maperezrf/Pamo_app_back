from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings

from .models import Dispatch, DispatchNotification, MarketplaceLabel, ShopifyFulfillmentOrder
from .tests_dispatch import BOCCHERINI, EMAIL_SETTINGS, ENVIA_LOCATION, F, PDF, RELATION, _location, _marketplace_row, _order
from .whatsapp_templates import WHATSAPP_TEMPLATES

URL = "/api/orders/dispatch/bulk-notify/"
CREATED = datetime(2026, 10, 20, 15, tzinfo=dt_timezone.utc)


@override_settings(**EMAIL_SETTINGS)
@patch(f"{F}.notify_dispatch.product_image_urls", return_value={})
@patch(f"{F}.dispatch_orders.fulfill_in_shopify")
class BulkNotifyTests(TestCase):
    """Botón "Notificar bodegas": pedidos agrupados por bodega y un solo
    mensaje por bodega con los que están listos."""

    def setUp(self):
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        # Exige guía.
        self.envia = _location(
            shopify_location_id=ENVIA_LOCATION, name="Bodega Envia", notify_email=True, emails=["envia@example.com"],
        )
        # No exige guía.
        self.boccherini = _location(notify_email=True, emails=["bocc@example.com"], requires_label=False)

    def _sodimac(self, shopify_id, *, label=True, **fields):
        order = _order(shopify_id=shopify_id, name=f"#{shopify_id}", marketplace="sodimac", created=CREATED, **fields)
        row = _marketplace_row(
            order, marketplace="sodimac", location_id=ENVIA_LOCATION, marketplace_order_id=f"oc{shopify_id}",
            marketplace_order_number=f"oc{shopify_id}", shipment_id="",
        )
        if label:
            MarketplaceLabel.objects.create(order=row, tracking_number=f"47{shopify_id}", label_pdf=PDF, relation_pdf=RELATION)
        return order

    def test_the_preview_groups_by_warehouse_with_ready_and_blocked(self, *_):
        self._sodimac("1")
        self._sodimac("2", label=False)  # esperando la guía del RPA
        madecentro = _order(shopify_id="3", name="#3", marketplace="madecentro", created=CREATED)
        _marketplace_row(madecentro, marketplace="madecentro", location_id=ENVIA_LOCATION)
        moved = self._sodimac("4")  # Shopify lo tiene en otra bodega
        ShopifyFulfillmentOrder.objects.create(
            order=moved, position=0, shopify_id="fo4", status="OPEN", location_id=BOCCHERINI, location_name="Boccherini",
        )
        bocc = _order(shopify_id="5", name="#5", marketplace="madecentro", created=CREATED)
        _marketplace_row(bocc, marketplace="madecentro")  # Madecentro en Boccherini, que no exige guía
        # Sodimac lleva su guía aunque la bodega no la exija.
        sodimac_bocc = _order(shopify_id="10", name="#10", marketplace="sodimac", created=CREATED)
        _marketplace_row(sodimac_bocc, marketplace="sodimac", marketplace_order_id="oc10", shipment_id="")
        novedad = _order(shopify_id="6", name="#6", created=CREATED)
        _marketplace_row(novedad, fulfillment_status="novedad", fulfillment_note="sin stock")
        # Fuera: ya avisado, anterior al flujo, cancelado.
        Dispatch.objects.create(
            shopify_order=self._sodimac("7"), location=self.envia, channel="sodimac", status=Dispatch.Status.NOTIFIED,
        )
        _marketplace_row(_order(shopify_id="8", created=datetime(2026, 10, 1, tzinfo=dt_timezone.utc)))
        self._sodimac("9", cancelled_at=CREATED)

        body = self.client.get(URL).json()

        groups = {group["name"]: group for group in body["locations"]}
        self.assertEqual(sorted(groups), ["Boccherini", "Bodega Envia"])
        envia = groups["Bodega Envia"]
        self.assertEqual([item["order_name"] for item in envia["ready"]], ["#1"])
        self.assertEqual(envia["ready"][0]["label"], "Guía 471")
        self.assertTrue(envia["requires_label"])
        reasons = {item["order_name"]: item["reason"] for item in envia["blocked"]}
        self.assertEqual(sorted(reasons), ["#2", "#3", "#4"])
        self.assertIn("Sodimac", reasons["#2"])
        self.assertIn("Falta la guía", reasons["#3"])
        self.assertIn("otra bodega: Boccherini", reasons["#4"])
        self.assertEqual([item["order_name"] for item in groups["Boccherini"]["ready"]], ["#5"])
        self.assertEqual(groups["Boccherini"]["ready"][0]["label"], "Sin guía (la bodega no la exige)")
        [sodimac_bocc] = groups["Boccherini"]["blocked"]
        self.assertEqual(sodimac_bocc["order_name"], "#10")
        self.assertIn("Sodimac", sodimac_bocc["reason"])
        self.assertEqual([item["order_name"] for item in body["unassigned"]], ["#6"])
        self.assertIn("sin stock", body["unassigned"][0]["reason"])
        self.assertFalse(Dispatch.objects.exclude(status=Dispatch.Status.NOTIFIED).exists())  # la vista previa no escribe

    def test_one_email_per_warehouse_with_all_its_ready_orders(self, fulfill, *_):
        self._sodimac("1")
        self._sodimac("2")
        self._sodimac("3", label=False)

        response = self.client.post(URL, {"location_id": self.envia.pk}, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(response.json()["notified"]), ["#1", "#2"])
        [message] = mail.outbox
        self.assertEqual(message.to, ["envia@example.com"])
        self.assertIn("2 pedidos", message.subject)
        self.assertIn("#1", message.body)
        self.assertIn("#2", message.body)
        self.assertEqual(
            sorted(name for name, _, _ in message.attachments),
            ["guia-#1.pdf", "guia-#2.pdf", "relacion-#1.pdf", "relacion-#2.pdf"],
        )
        self.assertEqual(Dispatch.objects.filter(status=Dispatch.Status.NOTIFIED).count(), 2)
        self.assertEqual(DispatchNotification.objects.filter(status="enviado").count(), 2)
        self.assertEqual(fulfill.call_count, 2)

        # Ya avisados: no se repiten; el que espera guía no se avisa.
        again = self.client.post(URL, {"location_id": self.envia.pk}, content_type="application/json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(len(mail.outbox), 1)

    @patch.dict(WHATSAPP_TEMPLATES["despacho_bodega"], name="despacho_bodega")
    @patch(f"{F}.label_links.BACKEND_PUBLIC_URL", "https://api.pamo.test")
    @patch(f"{F}.notify_dispatch.product_images", return_value={})
    @patch(f"{F}.notify_dispatch.send_whatsapp_template", return_value={"message_id": "m1"})
    @patch(f"{F}.notify_dispatch.upload_media", return_value="https://media.zernio.com/temp/l.pdf")
    def test_one_whatsapp_with_the_list_of_all_orders(self, upload, send_template, *_):
        self.envia.notify_email = False
        self.envia.notify_whatsapp = True
        self.envia.whatsapp_numbers = ["573001234567"]
        self.envia.save()
        self._sodimac("1")
        self._sodimac("2")

        self.client.post(URL, {"location_id": self.envia.pk}, content_type="application/json")

        upload.assert_called_once()
        self.assertEqual(upload.call_args.args[1], "despacho-1-y-1-mas.pdf")
        send_template.assert_called_once()
        self.assertEqual(send_template.call_args.kwargs["body_params"], ["Bodega Envia", "2"])

    def test_a_failed_email_leaves_every_order_in_error_to_retry(self, *_):
        self._sodimac("1")
        self._sodimac("2")

        with patch(f"{F}.notify_dispatch.EmailMultiAlternatives.send", side_effect=OSError("smtp caído")):
            response = self.client.post(URL, {"location_id": self.envia.pk}, content_type="application/json")

        self.assertEqual(sorted(response.json()["failed"]), ["#1", "#2"])
        self.assertEqual(Dispatch.objects.filter(status=Dispatch.Status.ERROR).count(), 2)
        self.assertEqual(DispatchNotification.objects.filter(status="error").count(), 2)
        # Siguen en la lista para reintentar.
        groups = {group["name"]: group for group in self.client.get(URL).json()["locations"]}
        self.assertEqual(len(groups["Bodega Envia"]["ready"]), 2)

    def test_unknown_warehouse_and_users_without_the_role(self, *_):
        self.assertEqual(self.client.post(URL, {"location_id": "x"}, content_type="application/json").status_code, 400)
        self.client.force_login(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.post(URL, {"location_id": self.envia.pk}, content_type="application/json").status_code, 403)
