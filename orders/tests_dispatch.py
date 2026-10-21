import io
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from pypdf import PdfReader

from .functions.assign_dispatch_location import assign_dispatch_location
from .functions import label_links
from .functions import process_shopify_order_webhook  # noqa: F401 -- los @patch lo resuelven como atributo
from .functions.dispatch_list_pdf import dispatch_list_pdf
from .functions.product_images import product_images
from .functions.dispatch_orders import dispatch_orders, ensure_dispatches, process_order_dispatch, split_location_ids
from .functions.shopify_fulfillment import tracking_company
from .functions.sync_dispatch_locations import sync_dispatch_locations
from .models import (
    Dispatch, DispatchLocation, DispatchNotification, MarketplaceLabel, MarketplaceOrder, ShopifyFulfillmentOrder,
    ShopifyFulfillmentOrderLine, ShopifyOrder, ShopifyOrderLine,
)
from .whatsapp_templates import WHATSAPP_TEMPLATES

F = "orders.functions"
PDF = b"%PDF-1.4 " + b"x" * 64 + b" %%EOF"
ENVIA_LOCATION = "97615380757"
BOCCHERINI = "99576545557"
EMAIL_SETTINGS = {"EMAIL_HOST": "smtp.test", "DEFAULT_FROM_EMAIL": "despachos@pamo.co"}
SINCE = {"since": "2026-10-08"}


def _location(shopify_location_id=BOCCHERINI, name="Boccherini", **overrides):
    return DispatchLocation.objects.create(shopify_location_id=shopify_location_id, name=name, **overrides)


def _order(shopify_id="5001", name="20400", marketplace="mercadolibre", created=datetime(2026, 10, 8, 15, tzinfo=dt_timezone.utc), **overrides):
    fields = dict(fulfillment_status="UNFULFILLED", total="78249.00", currency="COP")
    fields.update(overrides)
    order = ShopifyOrder.objects.create(
        shopify_id=shopify_id, name=name, marketplace=marketplace, shopify_created_at=created, **fields
    )
    ShopifyOrderLine.objects.create(order=order, position=0, sku="I001", name="Dispensador", quantity=1, unit_price="78249.00")
    return order


def _marketplace_row(order, location_id=BOCCHERINI, marketplace="mercadolibre", **overrides):
    defaults = dict(
        marketplace=marketplace,
        marketplace_order_id=f"mk-{order.shopify_id}",
        marketplace_order_number="2000015399650969",
        shipment_id="48195006881",
        shopify_order_id=order.shopify_id,
        status=MarketplaceOrder.Status.CREATED,
        fulfillment_status=MarketplaceOrder.FulfillmentStatus.ASSIGNED,
        fulfillment_location_id=location_id,
        fulfillment_location_name="Boccherini",
        customer_first_name="Ana",
        customer_last_name="Gómez",
        customer_city="Bogotá",
        customer_address="Calle 1",
        customer_phone="3000000000",
    )
    defaults.update(overrides)
    return MarketplaceOrder.objects.create(**defaults)


ML_SHIPMENT = {"status": "ready_to_ship", "substatus": "ready_for_pickup", "tracking_number": "MEL48195006881", "logistic_type": "cross_docking"}


class DispatchLocationTests(TestCase):
    @patch(f"{F}.sync_dispatch_locations.list_locations")
    def test_sync_creates_updates_and_deactivates_without_touching_channels(self, list_locations):
        configured = _location(name="Viejo nombre", notify_email=True, emails=["bodega@boccherini.com"])
        _location(shopify_location_id="111", name="Cerrada")
        list_locations.return_value = [
            {"location_id": BOCCHERINI, "name": "Boccherini", "is_active": True, "city": "Bogotá"},
            {"location_id": ENVIA_LOCATION, "name": "Bodega Envia", "is_active": True, "city": "Bogotá"},
        ]

        summary = sync_dispatch_locations()

        self.assertEqual(summary, {"created": 1, "updated": 1, "deactivated": 1})
        configured.refresh_from_db()
        self.assertEqual(configured.name, "Boccherini")
        self.assertTrue(configured.notify_email)
        self.assertEqual(configured.emails, ["bodega@boccherini.com"])
        self.assertFalse(DispatchLocation.objects.get(shopify_location_id="111").is_active)

    def test_each_enabled_channel_needs_its_contact_data(self):
        location = DispatchLocation(shopify_location_id="1", name="X", notify_api=True, notify_email=True, notify_whatsapp=True)
        with self.assertRaises(ValidationError) as raised:
            location.full_clean()
        self.assertEqual(set(raised.exception.message_dict), {"envia_warehouse_id", "emails", "whatsapp_numbers"})

        location.envia_warehouse_id = "311"
        location.emails = ["bodega@boccherini.com"]
        location.whatsapp_numbers = ["573001234567"]
        location.full_clean()
        self.assertEqual(location.channels(), ["api", "email", "whatsapp"])

    def test_rejects_badly_formatted_contacts(self):
        location = DispatchLocation(shopify_location_id="1", name="X", emails=["sin-arroba"], whatsapp_numbers=["+57 300"])
        with self.assertRaises(ValidationError) as raised:
            location.full_clean()
        self.assertEqual(set(raised.exception.message_dict), {"emails", "whatsapp_numbers"})


class AssignDispatchLocationTests(TestCase):
    def test_marketplace_order_keeps_the_location_chosen_at_import(self):
        location = _location()
        order = _order()
        _marketplace_row(order)
        self.assertEqual(assign_dispatch_location(order), (location, ""))

    def test_marketplace_novedad_goes_to_manual(self):
        _location()
        order = _order()
        _marketplace_row(order, fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD, fulfillment_location_id="", fulfillment_note="Sin stock")
        location, reason = assign_dispatch_location(order)
        self.assertIsNone(location)
        self.assertIn("Sin stock", reason)

    def test_madecentro_sodimac_and_unknown_marketplace_orders_go_to_manual(self):
        self.assertIsNone(assign_dispatch_location(_order(marketplace="sodimac"))[0])
        self.assertIsNone(assign_dispatch_location(_order(shopify_id="2", marketplace="madecentro"))[0])
        location, reason = assign_dispatch_location(_order(shopify_id="3", marketplace="falabella"))
        self.assertIsNone(location)
        self.assertIn("sin registro local", reason)

    @patch(f"{F}.assign_dispatch_location.FULFILLMENT_PRIORITY_LOCATION_ID", ENVIA_LOCATION)
    @patch(f"{F}.assign_dispatch_location.get_variant_inventory_by_sku")
    def test_web_order_uses_the_same_rule_with_live_inventory(self, inventory):
        envia = _location(shopify_location_id=ENVIA_LOCATION, name="Bodega Envia")
        inventory.return_value = {
            "variant_id": "1", "sku": "I001", "tracked": True,
            "locations": [{"location_id": ENVIA_LOCATION, "name": "Bodega Envia", "available": 5}],
        }
        self.assertEqual(assign_dispatch_location(_order(marketplace="shopify")), (envia, ""))

    @patch(f"{F}.assign_dispatch_location.get_variant_inventory_by_sku")
    def test_web_order_without_stock_or_unregistered_location_goes_to_manual(self, inventory):
        inventory.return_value = {"variant_id": "1", "sku": "I001", "tracked": True, "locations": []}
        location, reason = assign_dispatch_location(_order(marketplace="shopify"))
        self.assertIsNone(location)
        self.assertIn("Novedad", reason)

        inventory.return_value = {
            "variant_id": "1", "sku": "I001", "tracked": True,
            "locations": [{"location_id": "999", "name": "Nueva", "available": 5}],
        }
        location, reason = assign_dispatch_location(_order(shopify_id="2", marketplace="shopify"))
        self.assertIsNone(location)
        self.assertIn("sincronizar bodegas", reason)


class DispatchOrdersTests(TestCase):
    def setUp(self):
        self.location = _location(notify_email=True, emails=["bodega@boccherini.com"])
        self.order = _order()
        _marketplace_row(self.order)

    def _dispatch(self):
        return Dispatch.objects.get(shopify_order=self.order)

    def test_without_a_start_date_the_process_fails_instead_of_taking_everything(self):
        with self.assertRaisesMessage(ValueError, "since"):
            dispatch_orders({})
        with self.assertRaisesMessage(ValueError, "since"):
            dispatch_orders({"since": "8/10/2026"})
        self.assertFalse(Dispatch.objects.exists())

    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value={**ML_SHIPMENT, "status": "shipped", "substatus": ""})
    def test_old_and_fulfilled_orders_are_left_out(self, get_shipment):
        _order(shopify_id="old", created=datetime(2026, 10, 7, 4, 59, tzinfo=dt_timezone.utc))  # 6 oct en Colombia
        _order(shopify_id="done", fulfillment_status="FULFILLED")
        dispatch_orders(SINCE)
        self.assertEqual(list(Dispatch.objects.values_list("shopify_order__shopify_id", flat=True)), ["5001"])

    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value={**ML_SHIPMENT, "substatus": "in_packing_list", "status": "shipped"})
    def test_mercadolibre_guide_not_available_yet_waits(self, get_shipment):
        self.assertEqual(dispatch_orders(SINCE), {"esperando_guia": 1})
        self.assertIn("aún no disponible", self._dispatch().note)

    @override_settings(**EMAIL_SETTINGS)
    @patch(f"{F}.label_links.BACKEND_PUBLIC_URL", "https://api.pamo.test")
    @patch(f"{F}.notify_dispatch.product_image_urls", return_value={"I001": "https://cdn.shopify.com/i001.jpg"})
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value=ML_SHIPMENT)
    def test_emails_the_warehouse_once_with_the_guide(self, get_shipment, get_label, image_urls):
        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        [message] = mail.outbox
        self.assertEqual(message.to, ["bodega@boccherini.com"])
        self.assertIn("20400", message.subject)
        self.assertIn("I001 x1", message.body)
        self.assertIn("MEL48195006881", message.body)
        self.assertEqual(message.attachments[0][1], PDF)
        [(html, mimetype)] = message.alternatives  # la misma tabla del PDF
        self.assertEqual(mimetype, "text/html")
        self.assertIn('src="https://cdn.shopify.com/i001.jpg"', html)
        self.assertIn(">I001<", html)
        self.assertIn('href="https://api.pamo.test/api/orders/labels/', html)
        self.assertIn(">Destinatario<", html)  # columna de la tabla, por pedido
        notification = DispatchNotification.objects.get()
        self.assertEqual((notification.channel, notification.status), ("email", "enviado"))
        self.assertEqual(bytes(self._dispatch().label_pdf), PDF)  # la guía del canal queda guardada

        dispatch_orders(SINCE)  # ya notificado: no vuelve a enviar
        self.assertEqual(len(mail.outbox), 1)

    @patch.dict(WHATSAPP_TEMPLATES["despacho_bodega"], name="despacho_bodega")
    @patch(f"{F}.label_links.BACKEND_PUBLIC_URL", "https://api.pamo.test")
    @patch(f"{F}.notify_dispatch.product_images", return_value={})
    @patch(f"{F}.notify_dispatch.send_whatsapp_template", side_effect=[{"message_id": "m1"}, {"message_id": "m2"}])
    @patch(f"{F}.notify_dispatch.upload_media", return_value="https://media.zernio.com/temp/g.pdf")
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value=ML_SHIPMENT)
    def test_whatsapp_sends_the_list_pdf_with_a_working_guide_link(self, get_shipment, get_label, upload, send_template, images):
        self.location.notify_email = False
        self.location.notify_whatsapp = True
        self.location.whatsapp_numbers = ["573001234567", "573007654321"]
        self.location.save()

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        upload.assert_called_once()  # un PDF para todos los números
        list_pdf, filename = upload.call_args.args
        self.assertEqual(filename, "despacho-20400.pdf")
        page = PdfReader(io.BytesIO(list_pdf)).pages[0]
        self.assertIn("I001", page.extract_text())
        [link] = [annotation.get_object()["/A"]["/URI"] for annotation in page["/Annots"]]
        response = self.client.get(link.removeprefix("https://api.pamo.test"))  # la bodega, sin sesión
        self.assertEqual(response.content, PDF)

        self.assertEqual(send_template.call_count, 2)
        for call in send_template.call_args_list:
            self.assertEqual(call.args[1], "despacho_bodega")
            self.assertEqual(call.kwargs["body_params"], ["Boccherini", "1"])
            self.assertEqual(call.kwargs["document_url"], "https://media.zernio.com/temp/g.pdf")
        self.assertEqual(DispatchNotification.objects.get().external_id, "m1,m2")

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="")  # sin depender del .env
    @patch.dict(WHATSAPP_TEMPLATES["despacho_bodega"], name="")
    def test_unconfigured_email_whatsapp_and_unknown_envia_sku_are_errors_not_sends(self):
        self.location.requires_label = False
        self.location.notify_whatsapp = True
        self.location.whatsapp_numbers = ["573001234567"]
        self.location.notify_api = True
        self.location.envia_warehouse_id = "311"
        self.location.save()

        with patch(f"{F}.notify_dispatch.create_order") as create_order, \
                patch(f"{F}.notify_dispatch.send_whatsapp_template") as send_template, \
                patch(f"{F}.notify_dispatch.find_order", return_value=None), \
                patch(f"{F}.notify_dispatch.find_variant_ids", return_value={}):
            self.assertEqual(dispatch_orders(SINCE), {"error": 1})
            create_order.assert_not_called()
            send_template.assert_not_called()

        errors = dict(DispatchNotification.objects.values_list("channel", "error_description"))
        self.assertIn("EMAIL_HOST", errors["email"])
        self.assertIn("despacho_bodega", errors["whatsapp"])
        self.assertIn("SKU sin producto en Envía: I001", errors["api"])
        self.assertEqual(len(mail.outbox), 0)

    @patch.dict(WHATSAPP_TEMPLATES["despacho_bodega"], name="despacho_bodega")
    @patch(f"{F}.notify_dispatch.product_images", return_value={})
    @patch(f"{F}.notify_dispatch.send_whatsapp_template", return_value={"message_id": "wamid.1"})
    @patch(f"{F}.notify_dispatch.upload_media", return_value="https://media.zernio.com/temp/l.pdf")
    def test_whatsapp_without_guide_still_sends_the_list(self, upload, send_template, images):
        self.location.requires_label = False
        self.location.notify_email = False
        self.location.notify_whatsapp = True
        self.location.whatsapp_numbers = ["573001234567"]
        self.location.save()

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        list_pdf, _ = upload.call_args.args
        self.assertIn("Sin gu", PdfReader(io.BytesIO(list_pdf)).pages[0].extract_text())
        args, kwargs = send_template.call_args
        self.assertEqual(args, ("573001234567", "despacho_bodega"))
        self.assertEqual(kwargs["body_params"], ["Boccherini", "1"])
        self.assertEqual(kwargs["document_url"], "https://media.zernio.com/temp/l.pdf")
        self.assertEqual(DispatchNotification.objects.get().external_id, "wamid.1")

    @override_settings(**EMAIL_SETTINGS)
    def test_a_failed_notification_is_retried_and_a_processing_one_is_not(self):
        self.location.requires_label = False
        self.location.save()
        with patch(f"{F}.notify_dispatch.EmailMultiAlternatives.send", side_effect=OSError("smtp caído")):
            self.assertEqual(dispatch_orders(SINCE), {"error": 1})

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})  # reintento
        self.assertEqual(len(mail.outbox), 1)

        other = _order(shopify_id="5002", name="20401")
        _marketplace_row(other, marketplace_order_id="mk-2")
        dispatch = Dispatch.objects.create(shopify_order=other, location=self.location, channel="mercadolibre", status="error")
        DispatchNotification.objects.create(dispatch=dispatch, channel="email", status="procesando")
        dispatch_orders(SINCE)
        self.assertEqual(len(mail.outbox), 1)  # el que quedó procesando no se reenvía

    def test_location_without_channels_goes_to_manual(self):
        self.location.notify_email = False
        self.location.save()
        self.assertEqual(dispatch_orders(SINCE), {"manual": 1})
        self.assertIn("no tiene canales", self._dispatch().note)

    def test_cancelled_order_gets_a_cancelled_dispatch_and_leaves_the_flow(self):
        self.order.cancelled_at = self.order.shopify_created_at
        self.order.save()
        self.assertEqual(dispatch_orders(SINCE), {"cancelado": 1})
        self.assertEqual(dispatch_orders(SINCE), {})

    @patch(f"{F}.fetch_dispatch_label.get_shipping_document", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_package_items")
    def test_falabella_guide_comes_from_the_package_items(self, package_items, get_document):
        order = _order(shopify_id="6001", name="20402", marketplace="falabella")
        _marketplace_row(order, marketplace="falabella", marketplace_order_id="8001737501", shipment_id="")
        package_items.return_value = [
            {"order_item_id": "11", "package_id": "PKG1", "tracking_code": "2298450467", "status": "ready_to_ship"},
            {"order_item_id": "12", "package_id": "", "tracking_code": "", "status": "pending"},
        ]

        dispatch_orders({**SINCE, "shopify_order_ids": ["6001"]})

        get_document.assert_called_once_with(["11"])
        self.assertEqual(Dispatch.objects.get(shopify_order=order).tracking_number, "2298450467")


class OrderListDispatchTests(TestCase):
    def test_the_listing_includes_the_dispatch(self):
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        location = _location()
        order = _order()
        dispatch = Dispatch.objects.create(shopify_order=order, location=location, channel="mercadolibre", status="notificado")
        DispatchNotification.objects.create(dispatch=dispatch, channel="email", status="enviado", recipient="bodega@boccherini.com")
        _order(shopify_id="5002", name="20401")

        orders = {row["shopify_order_name"]: row for row in self.client.get("/api/orders/").json()["orders"]}

        [listed] = orders["20400"]["dispatches"]
        self.assertEqual((listed["id"], listed["split"]), (dispatch.pk, False))
        self.assertEqual((listed["location_id"], listed["location_name"]), (BOCCHERINI, "Boccherini"))
        self.assertEqual(listed["items"], [{"sku": "I001", "name": "Dispensador", "quantity": 1}])
        self.assertEqual(listed["notifications"][0]["channel"], "email")
        self.assertIsNone(listed["shopify_fulfilled_at"])
        self.assertEqual(listed["shopify_fulfillment_error"], "")
        self.assertEqual(orders["20401"]["dispatches"], [])


class EnviaIdentifierTests(TestCase):
    def test_is_the_bare_channel_number_or_the_shopify_number(self):
        from .functions.notify_dispatch import _envia_identifier

        # Operaciones pidió ver solo el número (2026-10-08), sin "sodimac-".
        self.assertEqual(_envia_identifier({"marketplace_numbers": ["16200704"], "order_name": "20400"}), "16200704")
        self.assertEqual(_envia_identifier({"marketplace_numbers": [], "order_name": "#20412"}), "20412")


N = f"{F}.notify_dispatch"
ENVIA_EXISTING = {"order_id": 49607578, "identifier": "20400", "warehouse_id": 311, "warehouse_status": "CREATED",
                  "fulfillment_status": "unfulfilled", "shop_name": "Pamo.co", "pretrackings": []}


class EnviaNotifierTests(TestCase):
    def setUp(self):
        self.location = _location(
            shopify_location_id=ENVIA_LOCATION, name="Bodega Envia", notify_api=True, envia_warehouse_id="311",
            requires_label=False,
        )
        self.order = _order(marketplace="sodimac")
        _marketplace_row(
            self.order, marketplace="sodimac", location_id=ENVIA_LOCATION, marketplace_order_number="16200704",
            customer_email="c@example.com", customer_region="MAGDALENA", customer_city="SANTA MARTA",
            customer_first_name="Ana María", customer_last_name="",
        )
        # Se prueba el notificador con el despacho ya asignado (bodega sin
        # exigencia de guía).
        self.dispatch = Dispatch.objects.create(
            shopify_order=self.order, location=self.location, channel="sodimac", status=Dispatch.Status.WAITING_LABEL
        )

    @patch(f"{N}.add_order_tracking")
    @patch(f"{N}.create_order")
    @patch(f"{N}.find_order", return_value=ENVIA_EXISTING)
    def test_links_the_order_envia_already_has_instead_of_creating_another(self, find_order, create_order, add_tracking):
        dispatch_orders(SINCE)

        create_order.assert_not_called()
        add_tracking.assert_not_called()  # no hay guía aquí
        notification = DispatchNotification.objects.get()
        self.assertEqual((notification.status, notification.external_id), ("enviado", "49607578"))
        self.assertIn("orden existente de Pamo.co", notification.recipient)

    @patch(f"{N}.resolve_colombia_state", return_value="MA")
    @patch(f"{N}.find_variant_ids", return_value={"I001": {"variant_id": 215359, "ecart_id": "1", "available": {311: 33}}})
    @patch(f"{N}.create_order", return_value={"order_id": 49690001, "identifier": "16200704", "warehouse_status": "PENDING"})
    @patch(f"{N}.find_order", return_value=None)
    def test_creates_the_order_with_envia_variant_state_code_and_split_name(self, find_order, create_order, variants, state):
        dispatch_orders(SINCE)

        kwargs = create_order.call_args.kwargs
        self.assertEqual(kwargs["identifier"], "16200704")
        self.assertEqual(kwargs["warehouse_id"], "311")
        self.assertEqual(kwargs["products"], [{"variant_id": 215359, "quantity": 1, "price": "78249.00"}])
        self.assertEqual(kwargs["shipping_address"]["state_code"], "MA")
        self.assertEqual(
            (kwargs["shipping_address"]["first_name"], kwargs["shipping_address"]["last_name"]), ("Ana", "María")
        )
        self.assertEqual(DispatchNotification.objects.get().external_id, "49690001")
        # Se buscó con nuestro identificador y con el número de Shopify.
        self.assertEqual([c.args[0] for c in find_order.call_args_list], ["16200704", "20400"])

    @patch(f"{N}.find_variant_ids", return_value={"I001": {"variant_id": 215359, "ecart_id": "1", "available": {}}})
    @patch(f"{N}.find_order", return_value=None)
    def test_missing_delivery_data_is_an_error_before_calling_envia(self, find_order, variants):
        MarketplaceOrder.objects.update(customer_address="")
        with patch(f"{N}.create_order") as create_order:
            dispatch_orders(SINCE)
            create_order.assert_not_called()
        self.assertIn(
            "Faltan datos de entrega para Envía: dirección", DispatchNotification.objects.get().error_description
        )


A = f"{F}.dispatch_actions"
QUOTE_OPTION = {"carrier": "tcc", "carrierLabel": "TCC", "service": "mensajeria", "price": 13840.0,
                "currency": "COP", "etaMinDays": 1, "etaMaxDays": 2, "providerPayload": {}}
WEB_SHIPPING = {"first_name": "Ana", "last_name": "Gómez", "company": "", "address1": "Calle 9", "address2": "",
                "city": "Bogotá", "province": "Bogotá", "province_code": "DC", "zip": "", "phone": "3001112233"}
GENERATED = {"tracking_number": "474231399", "label_url": "https://api.envia.com/l.pdf", "shipment_id": "1",
             "carrier": "tcc", "service": "mensajeria", "raw": {}}


class DispatchActionAPITests(TestCase):
    def setUp(self):
        self.location = _location(
            notify_email=True, emails=["bodega@boccherini.com"], requires_label=False,
            origin_address="Calle 1", origin_phone="3000000000", origin_province_code="DC", city="Bogotá",
        )
        self.user = User.objects.create_user("ops", password="x")
        self.user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(self.user)

    def _web_order(self):
        order = _order(shopify_id="7001", name="20412", marketplace="shopify")
        Dispatch.objects.create(
            shopify_order=order, location=self.location, channel="shopify", status=Dispatch.Status.WAITING_LABEL
        )
        return order

    def _post(self, order, path, body=None):
        return self.client.post(
            f"/api/orders/{order.shopify_id}/dispatch/{path}", body or {}, content_type="application/json"
        )

    @override_settings(**EMAIL_SETTINGS)
    def test_notify_button_sends_even_with_the_automation_switch_off(self):
        order = self._web_order()

        response = self._post(order, "notify/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dispatch"]["status"], "notificado")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(**EMAIL_SETTINGS)
    def test_notify_reopens_a_manual_dispatch_once_the_cause_is_fixed(self):
        # Se le dio al botón antes de sincronizar bodegas: quedó manual.
        order = _order(shopify_id="9101", name="20482")
        _marketplace_row(order, location_id=BOCCHERINI)
        Dispatch.objects.create(
            shopify_order=order, channel="mercadolibre", status=Dispatch.Status.MANUAL,
            note="La bodega 99576545557 no está en el registro: sincronizar bodegas.",
        )

        response = self._post(order, "notify/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dispatch"]["status"], "notificado")
        self.assertEqual(Dispatch.objects.get(shopify_order=order).location, self.location)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_manual_dispatch_whose_cause_remains_stays_manual_with_the_current_reason(self):
        order = _order(shopify_id="9102", name="20483")
        _marketplace_row(order, location_id="000", fulfillment_status=MarketplaceOrder.FulfillmentStatus.NOVEDAD,
                         fulfillment_note="sin stock")
        Dispatch.objects.create(shopify_order=order, channel="mercadolibre", status=Dispatch.Status.MANUAL, note="viejo")

        self.assertEqual(self._post(order, "notify/").json()["dispatch"]["status"], "manual")
        self.assertIn("sin stock", Dispatch.objects.get(shopify_order=order).note)
        self.assertEqual(len(mail.outbox), 0)

    def test_buttons_reject_users_without_the_role(self):
        order = self._web_order()
        self.client.force_login(User.objects.create_user("otro", password="x"))
        for path in ("notify/", "label/fetch/", "label/quote/", "label/"):
            self.assertEqual(self._post(order, path).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    @patch(f"{A}.quote", return_value=[QUOTE_OPTION])
    @patch(f"{A}.resolve_colombia_city", return_value={"city": "11001000", "state": "DC"})
    @patch(f"{A}.get_order_shipping_address", return_value=WEB_SHIPPING)
    def test_quote_returns_the_options_from_the_dispatch_warehouse(self, shipping, city, quote):
        order = self._web_order()
        ShopifyOrder.objects.filter(pk=order.pk).update(customer_identification="80196785")

        response = self._post(order, "label/quote/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["options"][0]["carrier"], "tcc")
        self.assertNotIn("providerPayload", response.json()["options"][0])
        self.assertEqual(response.json()["route"], {
            "origin": {"name": "Boccherini", "city": "Bogotá"},
            "destination": {"name": "Ana Gómez", "address": "Calle 9", "city": "Bogotá", "region": "Bogotá",
                            "phone": "3001112233"},
        })
        payload = quote.call_args.args[0]
        self.assertEqual(payload["origin"]["street"], "Calle 1")
        self.assertEqual(payload["destination"]["street"], "Calle 9")
        self.assertEqual(payload["destination"]["identificationNumber"], "80196785")  # la exigen algunas (#20598)
        self.assertEqual(payload["packages"][0]["weight"], 1)
        self.assertEqual(
            response.json()["packages"], [{"weight": 1, "length": 10, "width": 10, "height": 10, "quantity": 1}]
        )

    @patch(f"{A}.quote", return_value=[QUOTE_OPTION])
    @patch(f"{A}.resolve_colombia_city", return_value={"city": "11001000", "state": "DC"})
    @patch(f"{A}.get_order_shipping_address", return_value=WEB_SHIPPING)
    def test_quote_and_generate_use_the_package_from_the_panel(self, shipping, city, quote):
        order = self._web_order()
        package = {"weight": "2,5", "length": 30, "width": 20, "height": "15"}

        response = self._post(order, "label/quote/", {"package": package})

        self.assertEqual(response.status_code, 200)
        # `package` (una sola caja, el panel anterior) sigue sirviendo.
        expected = [{"weight": 2.5, "length": 30.0, "width": 20.0, "height": 15.0, "quantity": 1}]
        self.assertEqual(response.json()["packages"], expected)
        sent = quote.call_args.args[0]["packages"][0]
        self.assertEqual((sent["weight"], sent["dimensions"]), (2.5, {"length": 30, "width": 20, "height": 15}))

        with patch(f"{A}.create_label", return_value=GENERATED) as create_label, patch(
            f"{A}.download_label", return_value=PDF
        ):
            response = self._post(order, "label/", {"carrier": "tcc", "service": "mensajeria", "package": package})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_label.call_args.args[0]["packages"][0]["weight"], 2.5)
        self.assertEqual(Dispatch.objects.get(shopify_order=order).label_package, expected)
        self.assertEqual(response.json()["dispatch"]["label_package"], expected)

    @patch(f"{A}.quote", return_value=[QUOTE_OPTION])
    @patch(f"{A}.resolve_colombia_city", return_value={"city": "11001000", "state": "DC"})
    @patch(f"{A}.get_order_shipping_address", return_value=WEB_SHIPPING)
    def test_several_boxes_go_in_one_label_with_their_quantities(self, shipping, city, quote):
        order = self._web_order()  # total 78249
        packages = [
            {"weight": "2", "length": 30, "width": 20, "height": 15, "quantity": "3"},
            {"weight": 10, "length": 60, "width": 40, "height": 40},
        ]

        response = self._post(order, "label/quote/", {"packages": packages})

        self.assertEqual(response.status_code, 200)
        sent = quote.call_args.args[0]["packages"]
        self.assertEqual([(box["amount"], box["weight"]) for box in sent], [(3, 2.0), (1, 10.0)])
        self.assertEqual([box["declaredValue"] for box in sent], [19562.25, 19562.25])  # 78249 / 4 cajas
        self.assertEqual([row["quantity"] for row in response.json()["packages"]], [3, 1])

        with patch(f"{A}.create_label", return_value=GENERATED) as create_label, patch(
            f"{A}.download_label", return_value=PDF
        ):
            response = self._post(order, "label/", {"carrier": "tcc", "service": "mensajeria", "packages": packages})

        self.assertEqual(response.status_code, 200)
        create_label.assert_called_once()  # una sola guía
        self.assertEqual(len(create_label.call_args.args[0]["packages"]), 2)
        self.assertEqual([row["quantity"] for row in response.json()["dispatch"]["label_package"]], [3, 1])

    def test_a_label_generated_with_one_package_is_listed_as_a_list(self):
        order = self._web_order()
        Dispatch.objects.filter(shopify_order=order).update(
            label_source="envia", tracking_number="1", label_package={"weight": 0.5, "length": 10, "width": 10, "height": 5}
        )
        response = self.client.get("/api/orders/")
        dispatch = next(o for o in response.json()["orders"] if o["shopify_order_id"] == order.shopify_id)["dispatches"][0]
        self.assertEqual(dispatch["label_package"], [{"weight": 0.5, "length": 10, "width": 10, "height": 5, "quantity": 1}])

    @patch(f"{A}.quote")
    @patch(f"{A}.create_label")
    def test_an_invalid_quantity_or_too_many_rows_is_400(self, create_label, quote):
        order = self._web_order()
        box = {"weight": 1, "length": 10, "width": 10, "height": 10}
        for packages in ([{**box, "quantity": 0}], [{**box, "quantity": "1.5"}], [{**box, "quantity": 51}], [box] * 21):
            response = self._post(order, "label/quote/", {"packages": packages})
            self.assertEqual(response.status_code, 400, packages)
        response = self._post(order, "label/quote/", {"packages": [box, {**box, "weight": 0}]})
        self.assertIn("(paquete 2)", response.json()["detail"])
        quote.assert_not_called()
        create_label.assert_not_called()

    @patch(f"{A}.quote")
    @patch(f"{A}.create_label")
    def test_an_invalid_package_is_400_without_calling_envia(self, create_label, quote):
        order = self._web_order()
        for package in ({"weight": 0, "length": 10, "width": 10, "height": 10},
                        {"weight": 1, "length": -5, "width": 10, "height": 10},
                        {"weight": "uno", "length": 10, "width": 10, "height": 10},
                        {"weight": 1, "length": 10, "width": 10}):
            response = self._post(order, "label/quote/", {"package": package})
            self.assertEqual(response.status_code, 400, package)
            self.assertIn("mayor que 0", response.json()["detail"])
            body = {"carrier": "tcc", "service": "mensajeria", "package": package}
            self.assertEqual(self._post(order, "label/", body).status_code, 400, package)
        quote.assert_not_called()
        create_label.assert_not_called()

    @patch(f"{A}.download_label", return_value=PDF)
    @patch(f"{A}.create_label", return_value=GENERATED)
    @patch(f"{A}._shipping_payload", return_value={})
    def test_generate_saves_the_label_once_and_refuses_a_second(self, payload, create_label, download):
        order = self._web_order()
        body = {"carrier": "tcc", "service": "mensajeria"}

        response = self._post(order, "label/", body)

        self.assertEqual(response.status_code, 200)
        dispatch = Dispatch.objects.get(shopify_order=order)
        self.assertEqual(
            (dispatch.label_source, dispatch.tracking_number, dispatch.label_carrier), ("envia", "474231399", "tcc")
        )
        self.assertEqual(create_label.call_args.kwargs["order_reference"], "pamo-20412")
        self.assertEqual(bytes(dispatch.label_pdf), PDF)
        self.assertEqual(self._post(order, "label/", body).status_code, 400)
        self.assertEqual(create_label.call_count, 1)

        file = self.client.get(f"/api/orders/{order.shopify_id}/dispatch/label/file/")
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.content, PDF)
        self.assertIn('filename="pamo-20412-guia-474231399.pdf"', file["Content-Disposition"])
        self.assertEqual(download.call_count, 1)

    @patch(f"{F}.dispatch_orders.download_label", return_value=PDF)
    def test_a_label_generated_before_storing_pdfs_is_downloaded_once_and_kept(self, download):
        order = self._web_order()
        Dispatch.objects.filter(shopify_order=order).update(
            label_source="envia", tracking_number="2298971660", label_url="https://api.envia.com/l.pdf"
        )
        url = f"/api/orders/{order.shopify_id}/dispatch/label/file/"

        self.assertEqual(self.client.get(url).content, PDF)
        self.assertEqual(self.client.get(url).content, PDF)
        download.assert_called_once_with("https://api.envia.com/l.pdf")
        self.assertEqual(bytes(Dispatch.objects.get(shopify_order=order).label_pdf), PDF)

    def test_label_file_is_404_without_a_generated_label_and_403_without_the_role(self):
        order = self._web_order()
        url = f"/api/orders/{order.shopify_id}/dispatch/label/file/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(User.objects.create_user("otro2", password="x"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def _with_envia_label(self, order):
        Dispatch.objects.filter(shopify_order=order).update(
            label_source="envia", tracking_number="2298972094", label_url="https://api.envia.com/l.pdf",
            label_carrier="servientrega", label_service="premier", label_pdf=PDF, label_package={"weight": 0.5},
        )

    @patch(f"{A}.download_label", return_value=PDF)
    @patch(f"{A}.create_label", return_value=GENERATED)
    @patch(f"{A}._shipping_payload", return_value={})
    def test_release_frees_the_dispatch_for_a_new_label_and_notes_the_old_one(self, payload, create_label, download):
        order = self._web_order()
        self._with_envia_label(order)

        response = self._post(order, "label/release/")

        self.assertEqual(response.status_code, 200)
        dispatch = Dispatch.objects.get(shopify_order=order)
        self.assertEqual((dispatch.label_source, dispatch.tracking_number, dispatch.label_pdf), ("", "", None))
        self.assertIn("2298972094 (servientrega premier)", dispatch.note)
        self.assertEqual(self._post(order, "label/", {"carrier": "tcc", "service": "mensajeria"}).status_code, 200)
        self.assertEqual(Dispatch.objects.get(shopify_order=order).tracking_number, "474231399")

    def test_release_is_refused_once_the_warehouse_was_notified_or_without_a_label(self):
        order = self._web_order()
        self.assertEqual(self._post(order, "label/release/").status_code, 400)
        self._with_envia_label(order)
        dispatch = Dispatch.objects.get(shopify_order=order)
        DispatchNotification.objects.create(dispatch=dispatch, channel="email", status="enviado")

        response = self._post(order, "label/release/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Dispatch.objects.get(shopify_order=order).tracking_number, "2298972094")

    def _upload(self, order, pdf=PDF, **fields):
        data = {"tracking_number": "700123", "carrier": "TCC", **fields}
        if pdf is not None:
            data["label"] = SimpleUploadedFile("guia.pdf", pdf, content_type="application/pdf")
        return self.client.post(f"/api/orders/{order.shopify_id}/dispatch/label/upload/", data)

    def test_upload_stores_a_manual_label_used_for_shopify_and_download(self):
        order = self._web_order()

        response = self._upload(order)

        self.assertEqual(response.status_code, 200)
        dispatch = Dispatch.objects.get(shopify_order=order)
        self.assertEqual((dispatch.label_source, dispatch.tracking_number, dispatch.label_carrier), ("manual", "700123", "TCC"))
        self.assertEqual(bytes(dispatch.label_pdf), PDF)
        self.assertEqual(tracking_company(dispatch), "TCC")
        file = self.client.get(f"/api/orders/{order.shopify_id}/dispatch/label/file/")
        self.assertEqual(file.content, PDF)
        # Con guía no se carga ni se genera otra hasta liberarla.
        self.assertEqual(self._upload(order, tracking_number="999").status_code, 400)
        self.assertEqual(self._post(order, "label/quote/").status_code, 400)
        self.assertEqual(self._post(order, "label/release/").status_code, 200)
        self.assertEqual(self._upload(order, tracking_number="999").status_code, 200)

    def test_upload_rejects_a_non_pdf_or_missing_fields(self):
        order = self._web_order()
        self.assertEqual(self._upload(order, pdf=b"hola").status_code, 400)
        self.assertEqual(self._upload(order, pdf=None).status_code, 400)
        self.assertEqual(self._upload(order, carrier="").status_code, 400)
        self.assertEqual(Dispatch.objects.get(shopify_order=order).label_source, "")

    def test_upload_is_403_without_the_role(self):
        order = self._web_order()
        self.client.force_login(User.objects.create_user("otro4", password="x"))
        self.assertEqual(self._upload(order).status_code, 403)

    def test_release_is_403_without_the_role(self):
        order = self._web_order()
        self._with_envia_label(order)
        self.client.force_login(User.objects.create_user("otro3", password="x"))

        self.assertEqual(self._post(order, "label/release/").status_code, 403)
        self.assertEqual(Dispatch.objects.get(shopify_order=order).label_source, "envia")

    @patch(f"{A}._shipping_payload", return_value={})
    def test_generate_with_an_unknown_result_is_502_and_noted(self, payload):
        from integrations.envia.functions.create_label import LabelUnknownResult

        order = self._web_order()
        body = {"carrier": "tcc", "service": "mensajeria"}
        with patch(f"{A}.create_label", side_effect=LabelUnknownResult("ENVIA_LABEL_UNKNOWN_RESULT (timeout)")):
            self.assertEqual(self._post(order, "label/", body).status_code, 502)
        self.assertIn("pamo-20412", Dispatch.objects.get(shopify_order=order).note)

    def test_marketplace_orders_with_their_own_guide_cannot_generate_one(self):
        order = _order(shopify_id="8001", name="20413", marketplace="mercadolibre")
        Dispatch.objects.create(
            shopify_order=order, location=self.location, channel="mercadolibre", status=Dispatch.Status.WAITING_LABEL
        )
        response = self._post(order, "label/quote/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Traer guía", response.json()["detail"])


W = f"{F}.assign_web_dispatch"
ENVIA_STOCK = {
    "variant_id": "1", "sku": "I001", "tracked": True,
    "locations": [{"location_id": ENVIA_LOCATION, "name": "Bodega Envia", "available": 5}],
}


@patch(f"{F}.assign_dispatch_location.FULFILLMENT_PRIORITY_LOCATION_ID", ENVIA_LOCATION)
@patch(f"{F}.assign_dispatch_location.get_variant_inventory_by_sku", return_value=ENVIA_STOCK)
class AssignWebDispatchTests(TestCase):
    def setUp(self):
        self.envia = _location(shopify_location_id=ENVIA_LOCATION, name="Bodega Envia")

    def test_a_paid_web_order_gets_its_warehouse_once(self, inventory):
        from .functions.assign_web_dispatch import assign_web_dispatch

        order = _order(marketplace="shopify", financial_status="PAID")

        [dispatch] = assign_web_dispatch(order.shopify_id)

        self.assertEqual(dispatch.location, self.envia)
        self.assertIsNone(assign_web_dispatch(order.shopify_id))  # ya tiene despacho
        self.assertEqual(inventory.call_count, 1)
        self.assertEqual(Dispatch.objects.count(), 1)
        self.assertFalse(DispatchNotification.objects.exists())  # no avisa a nadie

    def test_skips_unpaid_cancelled_fulfilled_and_marketplace_orders(self, inventory):
        from .functions.assign_web_dispatch import assign_web_dispatch

        cases = [
            _order(shopify_id="1", marketplace="shopify", financial_status="PENDING"),
            _order(shopify_id="2", marketplace="shopify", financial_status="PAID",
                   cancelled_at=datetime(2026, 10, 8, tzinfo=dt_timezone.utc)),
            _order(shopify_id="3", marketplace="shopify", financial_status="PAID", fulfillment_status="FULFILLED"),
            _order(shopify_id="4", marketplace="mercadolibre", financial_status="PAID"),
        ]
        for order in cases:
            self.assertIsNone(assign_web_dispatch(order.shopify_id))
        self.assertFalse(Dispatch.objects.exists())
        inventory.assert_not_called()

    def test_waits_while_the_warehouse_registry_is_empty(self, inventory):
        from .functions.assign_web_dispatch import assign_web_dispatch

        DispatchLocation.objects.all().delete()
        order = _order(marketplace="shopify", financial_status="PAID")
        self.assertIsNone(assign_web_dispatch(order.shopify_id))
        self.assertFalse(Dispatch.objects.exists())

    def test_an_inventory_error_does_not_break_the_caller(self, inventory):
        from .functions.assign_web_dispatch import assign_web_dispatch

        inventory.side_effect = ConnectionError("Shopify caído")
        order = _order(marketplace="shopify", financial_status="PAID")
        with self.assertLogs("orders.functions.assign_web_dispatch", level="ERROR"):
            self.assertIsNone(assign_web_dispatch(order.shopify_id))
        self.assertFalse(Dispatch.objects.exists())

    def test_the_listing_shows_the_dispatch_warehouse_for_web_orders(self, inventory):
        from .functions.assign_web_dispatch import assign_web_dispatch

        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)
        order = _order(marketplace="shopify", financial_status="PAID")
        assign_web_dispatch(order.shopify_id)

        [row] = self.client.get("/api/orders/").json()["orders"]

        self.assertEqual(row["fulfillment"], {
            "status": "asignada", "location_id": ENVIA_LOCATION, "location_name": "Bodega Envia", "note": "",
        })


class WebDispatchHooksTests(TestCase):
    @patch(f"{F}.process_shopify_order_webhook.assign_web_dispatch")
    @patch(f"{F}.process_shopify_order_webhook.upsert_shopify_order", return_value="created")
    @patch(f"{F}.process_shopify_order_webhook.get_order", return_value={"id": "5001", "name": "20400"})
    def test_the_order_webhook_assigns_after_saving(self, get_order, upsert, assign):
        from .functions.process_shopify_order_webhook import process_shopify_order_webhook

        process_shopify_order_webhook({"topic": "orders/create", "order_id": "5001"})

        assign.assert_called_once_with("5001")

    @patch(f"{F}.sync_shopify_orders.assign_web_dispatch", return_value=object())
    @patch(f"{F}.sync_shopify_orders.upsert_shopify_order", return_value="updated")
    @patch(f"{F}.sync_shopify_orders.list_orders_page")
    def test_only_the_reconciliation_assigns_not_the_backfill(self, page, upsert, assign):
        from .functions.sync_shopify_orders import iter_shopify_order_pages

        page.return_value = {"orders": [{"id": "5001"}], "has_next_page": False, "end_cursor": None}

        list(iter_shopify_order_pages("q"))  # carga inicial
        assign.assert_not_called()
        [(_, counts)] = list(iter_shopify_order_pages("q", assign_web_dispatches=True))  # reconciliación
        assign.assert_called_once_with("5001")
        self.assertEqual(counts["bodega_asignada"], 1)


class OwnLabelWarehouseTests(TestCase):
    """Operaciones (2026-10-08): Bodega Envia crea la guía de los pedidos de
    Shopify; otras bodegas necesitan que se la generemos."""

    def setUp(self):
        self.web = _order(marketplace="shopify", financial_status="PAID")

    def _dispatch(self, **location):
        place = _location(notify_email=True, emails=["bodega@example.com"], **location)
        Dispatch.objects.create(shopify_order=self.web, location=place, channel="shopify", status=Dispatch.Status.WAITING_LABEL)
        return place

    @override_settings(**EMAIL_SETTINGS)
    def test_a_warehouse_that_creates_its_label_is_notified_without_one(self):
        self._dispatch(creates_own_label=True)

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments, [])

    def test_otherwise_the_web_order_waits_for_a_generated_label(self):
        self._dispatch(creates_own_label=False)

        self.assertEqual(dispatch_orders(SINCE), {"esperando_guia": 1})
        self.assertEqual(len(mail.outbox), 0)

    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value={**ML_SHIPMENT, "substatus": "ready_to_print"})
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @override_settings(**EMAIL_SETTINGS)
    def test_mercadolibre_still_carries_its_channel_label(self, get_label, get_shipment):
        place = _location(shopify_location_id="777", name="Envia ML", notify_email=True, emails=["b@example.com"],
                          creates_own_label=True)
        order = _order(shopify_id="9001", name="20500")
        _marketplace_row(order, location_id="777", marketplace_order_id="ml-1")
        Dispatch.objects.create(shopify_order=order, location=place, channel="mercadolibre", status=Dispatch.Status.WAITING_LABEL)

        dispatch_orders({**SINCE, "shopify_order_ids": ["9001"]})

        get_label.assert_called_once()
        self.assertEqual(mail.outbox[0].attachments[0][1], PDF)

    def test_generate_label_is_refused_for_a_warehouse_that_creates_its_own(self):
        self._dispatch(creates_own_label=True)
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

        response = self.client.post(f"/api/orders/{self.web.shopify_id}/dispatch/label/quote/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("crea su propia guía", response.json()["detail"])


class TestNotificationsAdminTests(TestCase):
    """Página del admin "Probar avisos" (Bodegas de despacho): prueba los
    canales desde el servidor, sin CLI de Railway."""

    URL = "/admin/orders/dispatchlocation/probar-avisos/"

    def _login(self, **flags):
        user = User.objects.create_user("admin", password="x", is_staff=True, **flags)
        self.client.force_login(user)

    @override_settings(**EMAIL_SETTINGS)
    def test_a_superuser_sends_a_test_email(self):
        self._login(is_superuser=True)

        self.assertContains(self.client.get(self.URL), "Enviar correo de prueba")
        response = self.client.post(self.URL, {"channel": "email", "to": "yo@example.com"}, follow=True)

        self.assertContains(response, "Correo enviado a yo@example.com")
        self.assertEqual(mail.outbox[0].to, ["yo@example.com"])

    @patch(f"{F}.send_test_notification.send_whatsapp_template", return_value={"message_id": "wamid.1", "raw": {}})
    def test_a_superuser_sends_a_test_whatsapp(self, send):
        self._login(is_superuser=True)

        response = self.client.post(self.URL, {"channel": "whatsapp", "to": "573001234567"}, follow=True)

        self.assertContains(response, "WhatsApp enviado a 573001234567")
        send.assert_called_once_with("573001234567", "hello_world", language="en_US", body_params=())

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="")
    def test_a_missing_email_setup_is_shown_as_an_error(self):
        self._login(is_superuser=True)

        response = self.client.post(self.URL, {"channel": "email", "to": "yo@example.com"}, follow=True)

        self.assertContains(response, "Correo no configurado")
        self.assertEqual(mail.outbox, [])

    @override_settings(**EMAIL_SETTINGS)
    def test_staff_without_superuser_is_refused(self):
        self._login()

        self.assertEqual(self.client.post(self.URL, {"channel": "email", "to": "yo@example.com"}).status_code, 403)
        self.assertEqual(mail.outbox, [])


SODIMAC_ROW = dict(marketplace="sodimac", location_id=ENVIA_LOCATION, marketplace_order_id="16200704",
                   marketplace_order_number="16200704", shipment_id="")
RELATION = b"%PDF-1.4 relacion %%EOF"


@override_settings(**EMAIL_SETTINGS)
class SodimacAndMadecentroDispatchTests(TestCase):
    """2026-10-11: Sodimac espera la guía del RPA; Madecentro, una generada
    en Envía (aunque la bodega cree las de la tienda web)."""

    def _location(self, **overrides):
        return _location(
            shopify_location_id=ENVIA_LOCATION, name="Bodega Envia", notify_email=True, emails=["b@example.com"],
            creates_own_label=True, **overrides,
        )

    def test_sodimac_waits_for_the_uploaded_label_then_sends_both_pdfs(self):
        from .models import MarketplaceLabel

        self._location()
        order = _order(marketplace="sodimac")
        row = _marketplace_row(order, **SODIMAC_ROW)

        self.assertEqual(dispatch_orders(SINCE), {"esperando_guia": 1})
        self.assertIn("RPA", Dispatch.objects.get().note)
        self.assertEqual(len(mail.outbox), 0)

        MarketplaceLabel.objects.create(order=row, tracking_number="474231303", label_pdf=PDF, relation_pdf=RELATION)
        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        self.assertEqual(Dispatch.objects.get().tracking_number, "474231303")
        attachments = {name: content for name, content, _ in mail.outbox[0].attachments}
        self.assertEqual(attachments, {"guia-20400.pdf": PDF, "relacion-20400.pdf": RELATION})

    def test_a_sodimac_home_delivery_label_goes_without_relation(self):
        from .models import MarketplaceLabel

        self._location()
        row = _marketplace_row(_order(marketplace="sodimac"), **SODIMAC_ROW)
        MarketplaceLabel.objects.create(order=row, tracking_number="16200704", label_pdf=PDF)

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})
        self.assertEqual([name for name, _, _ in mail.outbox[0].attachments], ["guia-20400.pdf"])

    def test_madecentro_waits_for_a_generated_label_even_where_the_warehouse_creates_web_labels(self):
        self._location()
        order = _order(marketplace="madecentro")
        _marketplace_row(order, marketplace="madecentro", location_id=ENVIA_LOCATION, shipment_id="")

        self.assertEqual(dispatch_orders(SINCE), {"esperando_guia": 1})
        self.assertIn("Generar guía", Dispatch.objects.get().note)
        self.assertEqual(len(mail.outbox), 0)

    @patch(f"{A}.quote", return_value=[QUOTE_OPTION])
    @patch(f"{A}.resolve_colombia_city", return_value={"city": "05001000", "state": "AN"})
    @patch(f"{A}.get_order_shipping_address")
    def test_madecentro_quote_goes_to_the_buyer_address(self, shipping, city, quote):
        self._location(origin_address="Calle 1", origin_phone="3000000000", origin_province_code="CUN", city="Cota")
        order = _order(marketplace="madecentro")
        _marketplace_row(order, marketplace="madecentro", location_id=ENVIA_LOCATION, shipment_id="",
                         customer_address="Cra 50 # 10-20", customer_city="Medellín", customer_region="Antioquia",
                         customer_email="ana@example.com", customer_identification="1.234.567")
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

        response = self.client.post(f"/api/orders/{order.shopify_id}/dispatch/label/quote/")

        self.assertEqual(response.status_code, 200)
        shipping.assert_not_called()  # en Shopify va al cliente fijo, sin dirección
        destination = quote.call_args.args[0]["destination"]
        self.assertEqual((destination["street"], destination["email"]), ("Cra 50 # 10-20", "ana@example.com"))
        self.assertEqual(destination["identificationNumber"], "1.234.567")  # la del comprador, no la del cliente fijo
        route = response.json()["route"]["destination"]
        self.assertEqual((route["address"], route["city"], route["region"]), ("Cra 50 # 10-20", "Medellín", "Antioquia"))
        city.assert_called_with("Medellín", "Antioquia")


class EnviaRelationTests(TestCase):
    def setUp(self):
        from .models import MarketplaceLabel

        location = _location(
            shopify_location_id=ENVIA_LOCATION, name="Bodega Envia", notify_api=True, envia_warehouse_id="311"
        )
        order = _order(marketplace="sodimac")
        row = _marketplace_row(order, **SODIMAC_ROW, customer_email="c@example.com", customer_region="MAGDALENA",
                               customer_city="SANTA MARTA")
        MarketplaceLabel.objects.create(order=row, tracking_number="474231303", label_pdf=PDF, relation_pdf=RELATION)
        Dispatch.objects.create(
            shopify_order=order, location=location, channel="sodimac", status=Dispatch.Status.WAITING_LABEL
        )
        self.order = order

    def _notify(self):
        from .functions.dispatch_orders import process_order_dispatch

        created = {"order_id": 49690001, "identifier": "16200704", "warehouse_status": "PENDING"}
        variants = {"I001": {"variant_id": 1, "ecart_id": "1", "available": {}}}
        with patch(f"{N}.resolve_colombia_state", return_value="MA"), \
                patch(f"{N}.find_variant_ids", return_value=variants), \
                patch(f"{N}.find_order", return_value=None), \
                patch(f"{N}.create_order", return_value=created) as create:
            status = process_order_dispatch(self.order)
        return status, create

    @patch(f"{N}.add_order_comment")
    def test_the_relation_goes_as_a_comment_with_the_file(self, comment):
        status, create = self._notify()

        self.assertEqual(status, Dispatch.Status.NOTIFIED)
        self.assertEqual(create.call_args.kwargs["label_pdf"], PDF)
        self.assertEqual(comment.call_args.args[0], 49690001)
        self.assertEqual(comment.call_args.kwargs["pdf"], RELATION)

    @patch(f"{N}.add_order_comment", side_effect=ConnectionError("caído"))
    def test_a_failed_relation_is_a_warning_not_a_failed_notification(self, comment):
        status, _ = self._notify()

        self.assertEqual(status, Dispatch.Status.NOTIFIED)
        notification = DispatchNotification.objects.get()
        self.assertEqual(notification.status, "enviado")
        self.assertIn("relación no se cargó", notification.error_description)


S = "orders.functions.shopify_fulfillment"


def _fulfillment_order(location_id=BOCCHERINI, status="OPEN", tracking_numbers=(), gid="gid://shopify/FulfillmentOrder/1"):
    return {
        "id": gid, "status": status, "location_id": location_id, "location_name": "Bodega",
        "actions": {"CREATE_FULFILLMENT", "MOVE"}, "tracking_numbers": list(tracking_numbers),
    }


@override_settings(**EMAIL_SETTINGS)
@patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
@patch(f"{F}.fetch_dispatch_label.get_shipment", return_value=ML_SHIPMENT)
@patch(f"{S}.create_fulfillment", return_value="900")
@patch(f"{S}.move_fulfillment_order", return_value="gid://shopify/FulfillmentOrder/2")
@patch(f"{S}.get_fulfillment_orders")
class ShopifyFulfillmentTests(TestCase):
    """La guía queda en Shopify después de avisar a la bodega."""

    def setUp(self):
        self.location = _location(notify_email=True, emails=["bodega@boccherini.com"])
        self.order = _order()
        _marketplace_row(self.order)

    def _dispatch(self):
        return Dispatch.objects.get(shopify_order=self.order)

    def test_after_notifying_fulfills_with_the_guide_and_carrier(self, get_fos, move, create, *_):
        get_fos.return_value = [_fulfillment_order()]

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        self.assertEqual(len(mail.outbox), 1)  # el aviso salió antes
        move.assert_not_called()
        create.assert_called_once_with(
            "gid://shopify/FulfillmentOrder/1", tracking_number="MEL48195006881", tracking_company="Mercado Envíos"
        )
        dispatch = self._dispatch()
        self.assertIsNotNone(dispatch.shopify_fulfilled_at)
        self.assertEqual(dispatch.shopify_fulfillment_error, "")

    def test_moves_to_the_dispatch_location_before_fulfilling(self, get_fos, move, create, *_):
        get_fos.return_value = [_fulfillment_order(location_id="94018535701")]  # "Proveedores"

        dispatch_orders(SINCE)

        move.assert_called_once_with("gid://shopify/FulfillmentOrder/1", BOCCHERINI)
        self.assertEqual(create.call_args.args[0], "gid://shopify/FulfillmentOrder/2")

    def test_a_shopify_error_keeps_the_notice_and_the_button_retries(self, get_fos, move, create, *_):
        get_fos.return_value = [_fulfillment_order()]
        create.side_effect = RuntimeError("Shopify caído")

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})
        dispatch = self._dispatch()
        self.assertIsNone(dispatch.shopify_fulfilled_at)
        self.assertIn("Shopify caído", dispatch.shopify_fulfillment_error)

        create.side_effect = None
        self.assertEqual(process_order_dispatch(self.order), "notificado")  # botón "Notificar a proveedor"
        self.assertEqual(len(mail.outbox), 1)  # no vuelve a avisar
        self.assertIsNotNone(self._dispatch().shopify_fulfilled_at)

        process_order_dispatch(self.order)  # ya cumplido: no se repite
        self.assertEqual(create.call_count, 2)

    def test_already_fulfilled_by_hand_with_the_same_guide_is_marked_done(self, get_fos, move, create, *_):
        get_fos.return_value = [_fulfillment_order(status="CLOSED", tracking_numbers=["MEL48195006881"])]

        dispatch_orders(SINCE)

        create.assert_not_called()
        self.assertIsNotNone(self._dispatch().shopify_fulfilled_at)

    def test_fulfilled_with_another_guide_or_split_order_is_left_for_a_person(self, get_fos, move, create, *_):
        get_fos.return_value = [_fulfillment_order(status="CLOSED", tracking_numbers=["OTRA"])]
        dispatch_orders(SINCE)
        self.assertIn("OTRA", self._dispatch().shopify_fulfillment_error)

        get_fos.return_value = [_fulfillment_order(), _fulfillment_order(location_id=ENVIA_LOCATION, gid="gid://2")]
        process_order_dispatch(self.order)
        self.assertIn("varias bodegas", self._dispatch().shopify_fulfillment_error)
        create.assert_not_called()
        move.assert_not_called()

    def test_without_a_guide_nothing_is_sent_to_shopify(self, get_fos, move, create, *_):
        self.location.requires_label = False
        self.location.save()

        self.assertEqual(dispatch_orders(SINCE), {"notificado": 1})

        get_fos.assert_not_called()
        self.assertIsNone(self._dispatch().shopify_fulfilled_at)


class TrackingCompanyTests(TestCase):
    def setUp(self):
        self.location = _location()

    def _dispatch(self, order, **fields):
        return Dispatch.objects.create(shopify_order=order, location=self.location, channel=order.marketplace, status="notificado", **fields)

    def test_sodimac_is_tcc_except_home_delivery(self):
        tcc = _order(shopify_id="1", marketplace="sodimac")
        row = _marketplace_row(tcc, marketplace="sodimac")
        MarketplaceLabel.objects.create(order=row, tracking_number="474313505", label_pdf=PDF, relation_pdf=PDF)
        self.assertEqual(tracking_company(self._dispatch(tcc)), "TCC")

        home = _order(shopify_id="2", marketplace="sodimac")
        row = _marketplace_row(home, marketplace="sodimac", marketplace_order_id="16227888")
        MarketplaceLabel.objects.create(order=row, tracking_number="16227888", label_pdf=PDF)
        self.assertEqual(tracking_company(self._dispatch(home)), "Sodimac Home Delivery")

    def test_generated_in_envia_uses_its_carrier(self):
        dispatch = self._dispatch(_order(marketplace="madecentro"), label_source="envia", label_carrier="coordinadora")
        self.assertEqual(tracking_company(dispatch), "coordinadora")


BARU = "98784313621"


@patch(f"{A}.resolve_colombia_city", return_value={"city": "11001000", "state": "DC"})
@patch(f"{A}.get_order_shipping_address", return_value=WEB_SHIPPING)
class SplitDispatchTests(TestCase):
    """Pedido web que Shopify repartió en dos bodegas (como #20577): un
    despacho por bodega, cada uno con sus productos y su guía."""

    def setUp(self):
        address = dict(origin_address="Calle 1", origin_phone="3000000000", origin_province_code="DC", city="Bogotá")
        self.envia = _location(ENVIA_LOCATION, "Bodega Envia", notify_api=True, **address)
        self.baru = _location(BARU, "Baru", **address)
        self.order = _order(shopify_id="20577", name="20577", marketplace="shopify", total="300000.00")
        ShopifyOrderLine.objects.create(
            order=self.order, position=1, sku="CB8080L", name="Cabina", quantity=2, unit_price="100000.00"
        )
        self._fulfillment_order(BARU, "Baru", [("CB8080L", 2)])
        self._fulfillment_order(ENVIA_LOCATION, "Bodega Envia", [("I001", 1)])
        user = User.objects.create_user("ops", password="x")
        user.groups.add(Group.objects.get_or_create(name="Operaciones")[0])
        self.client.force_login(user)

    def _fulfillment_order(self, location_id, name, lines, status="OPEN"):
        position = self.order.fulfillment_orders.count()
        item = ShopifyFulfillmentOrder.objects.create(
            order=self.order, position=position, shopify_id=f"fo-{position}", status=status,
            location_id=location_id, location_name=name,
        )
        for index, (sku, quantity) in enumerate(lines):
            ShopifyFulfillmentOrderLine.objects.create(fulfillment_order=item, position=index, sku=sku, quantity=quantity)
        return item

    def _dispatch(self, location):
        return Dispatch.objects.get(shopify_order=self.order, location=location)

    def _post(self, path, body=None):
        return self.client.post(
            f"/api/orders/{self.order.shopify_id}/dispatch/{path}", body or {}, content_type="application/json"
        )

    def test_one_dispatch_per_shopify_warehouse_with_its_products(self, shipping, city):
        from .functions.dispatch_actions import DispatchActionError, dispatch_for_action
        from .functions.dispatch_details import dispatch_lines

        dispatches = ensure_dispatches(self.order)

        self.assertEqual([(d.location, d.split, d.status) for d in dispatches], [
            (self.baru, True, Dispatch.Status.WAITING_LABEL), (self.envia, True, Dispatch.Status.WAITING_LABEL),
        ])
        self.assertEqual(
            [(line["sku"], line["quantity"], line["unit_price"]) for line in dispatch_lines(dispatches[0])],
            [("CB8080L", 2, Decimal("100000.00"))],
        )
        self.assertEqual(ensure_dispatches(self.order), dispatches)  # no se duplican
        with self.assertRaisesMessage(DispatchActionError, "varias bodegas"):
            dispatch_for_action(self.order)

    def test_a_closed_fulfillment_order_or_a_marketplace_order_is_not_split(self, shipping, city):
        self.order.fulfillment_orders.filter(location_id=BARU).update(status="CLOSED")
        self.assertEqual(split_location_ids(self.order), [])
        self.order.fulfillment_orders.filter(location_id=BARU).update(status="OPEN")
        self.order.marketplace = "madecentro"
        self.assertEqual(split_location_ids(self.order), [])

    def test_a_manual_dispatch_without_activity_is_replaced_by_one_per_warehouse(self, shipping, city):
        # Lo que pasó con #20577: "ninguna bodega tiene el pedido completo".
        Dispatch.objects.create(shopify_order=self.order, channel="shopify", status=Dispatch.Status.MANUAL, note="Novedad")

        self.assertEqual(len(ensure_dispatches(self.order)), 2)
        self.assertFalse(Dispatch.objects.filter(shopify_order=self.order, location__isnull=True).exists())

    def test_a_dispatch_with_notifications_is_never_replaced(self, shipping, city):
        dispatch = Dispatch.objects.create(
            shopify_order=self.order, location=self.envia, channel="shopify", status=Dispatch.Status.ERROR
        )
        DispatchNotification.objects.create(dispatch=dispatch, channel="api", status="error")

        self.assertEqual(ensure_dispatches(self.order), [dispatch])

    def test_an_unregistered_warehouse_leaves_its_part_manual(self, shipping, city):
        self.baru.delete()

        dispatches = ensure_dispatches(self.order)

        self.assertEqual([(d.location, d.status) for d in dispatches], [
            (self.envia, Dispatch.Status.WAITING_LABEL), (None, Dispatch.Status.MANUAL),
        ])
        self.assertIn("Baru", dispatches[1].note)

    @patch(f"{A}.quote", return_value=[QUOTE_OPTION])
    def test_quote_and_generate_go_from_the_chosen_warehouse_with_its_products(self, quote, shipping, city):
        ensure_dispatches(self.order)
        baru = self._dispatch(self.baru)

        self.assertEqual(self._post("label/quote/").status_code, 400)  # sin dispatch_id
        response = self._post("label/quote/", {"dispatch_id": baru.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"]["origin"]["name"], "Baru")
        package = quote.call_args.args[0]["packages"][0]
        self.assertEqual((package["content"], package["declaredValue"]), ("Cabina", 200000.0))

        with patch(f"{A}.create_label", return_value=GENERATED) as create_label, patch(
            f"{A}.download_label", return_value=PDF
        ):
            response = self._post("label/", {"dispatch_id": baru.pk, "carrier": "tcc", "service": "mensajeria"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_label.call_args.kwargs["order_reference"], f"pamo-20577-{baru.pk}")
        self.assertEqual(response.json()["dispatch"]["id"], baru.pk)
        self.assertEqual(len(response.json()["dispatches"]), 2)
        self.assertEqual(self._dispatch(self.baru).tracking_number, "474231399")
        self.assertEqual(self._dispatch(self.envia).tracking_number, "")

        url = f"/api/orders/{self.order.shopify_id}/dispatch/label/file/"
        self.assertEqual(self.client.get(url).status_code, 400)  # sin dispatch_id
        file = self.client.get(url, {"dispatch_id": baru.pk})
        self.assertEqual(file.content, PDF)
        self.assertIn(f'filename="pamo-20577-{baru.pk}-guia-474231399.pdf"', file["Content-Disposition"])

    def test_a_dispatch_of_another_order_is_404(self, shipping, city):
        other = Dispatch.objects.create(
            shopify_order=_order(shopify_id="9", name="9"), channel="mercadolibre", status=Dispatch.Status.MANUAL
        )

        self.assertEqual(self._post("label/quote/", {"dispatch_id": other.pk}).status_code, 404)

    @override_settings(**EMAIL_SETTINGS)
    @patch(f"{F}.notify_dispatch.product_image_urls", return_value={})
    @patch(f"{F}.notify_dispatch.get_order_shipping_address", return_value={**WEB_SHIPPING, "province_code": "DC"})
    @patch(f"{F}.notify_dispatch.resolve_colombia_state", return_value="DC")
    @patch(f"{F}.notify_dispatch.find_variant_ids", return_value={"I001": {"variant_id": 215359, "ecart_id": "1", "available": {}}})
    @patch(f"{F}.notify_dispatch.create_order", return_value={"order_id": 50100001, "identifier": "x", "warehouse_status": "PENDING"})
    @patch(f"{F}.notify_dispatch.find_order", return_value=None)
    @patch(f"{S}.create_fulfillment", return_value="900")
    @patch(f"{S}.move_fulfillment_order")
    @patch(f"{S}.get_fulfillment_orders")
    def test_each_warehouse_is_notified_and_fulfilled_with_only_its_part(
        self, get_fos, move, create_fulfillment, find_order, create_order, variants, state, envia_shipping, images,
        shipping, city,
    ):
        DispatchLocation.objects.filter(pk=self.baru.pk).update(notify_email=True, emails=["baru@pamo.co"])
        DispatchLocation.objects.filter(pk=self.envia.pk).update(envia_warehouse_id="311")
        ShopifyOrder.objects.filter(pk=self.order.pk).update(email="ana@example.com")  # Envía exige correo
        ensure_dispatches(self.order)
        for location, tracking in ((self.baru, "111"), (self.envia, "222")):
            Dispatch.objects.filter(shopify_order=self.order, location=location).update(
                label_source="envia", tracking_number=tracking, label_carrier="tcc", label_pdf=PDF,
            )
        get_fos.return_value = [
            {**_fulfillment_order(BARU, gid="gid://shopify/FulfillmentOrder/1")},
            {**_fulfillment_order(ENVIA_LOCATION, gid="gid://shopify/FulfillmentOrder/2")},
        ]
        baru, envia = self._dispatch(self.baru), self._dispatch(self.envia)

        self.assertEqual(self._post("notify/", {"dispatch_id": baru.pk}).status_code, 200)

        [message] = mail.outbox  # solo Baru, solo con sus productos
        self.assertEqual(message.to, ["baru@pamo.co"])
        self.assertIn("CB8080L", message.body)
        self.assertNotIn("I001", message.body)
        create_order.assert_not_called()  # Bodega Envia todavía no
        create_fulfillment.assert_called_once_with("gid://shopify/FulfillmentOrder/1", tracking_number="111", tracking_company="tcc")

        self.assertEqual(self._post("notify/", {"dispatch_id": envia.pk}).status_code, 200)

        # Envía: orden propia solo con su parte; nunca la importada completa (número de Shopify).
        self.assertEqual([c.args[0] for c in find_order.call_args_list], [f"20577-{envia.pk}"])
        notification = DispatchNotification.objects.get(dispatch=envia)
        self.assertEqual(notification.status, "enviado", notification.error_description)
        kwargs = create_order.call_args.kwargs
        self.assertEqual((kwargs["identifier"], kwargs["products"]), (
            f"20577-{envia.pk}", [{"variant_id": 215359, "quantity": 1, "price": "78249.00"}],
        ))
        self.assertEqual(create_fulfillment.call_args.args[0], "gid://shopify/FulfillmentOrder/2")
        move.assert_not_called()  # cada parte se cumple en su bodega, sin mover nada
        self.assertEqual(
            [(d.status, bool(d.shopify_fulfilled_at)) for d in (self._dispatch(self.baru), self._dispatch(self.envia))],
            [(Dispatch.Status.NOTIFIED, True), (Dispatch.Status.NOTIFIED, True)],
        )

    @patch(f"{S}.create_fulfillment")
    @patch(f"{S}.get_fulfillment_orders")
    def test_a_split_part_already_fulfilled_with_its_guide_is_marked_done(self, get_fos, create_fulfillment, shipping, city):
        from .functions.shopify_fulfillment import fulfill_in_shopify

        ensure_dispatches(self.order)
        Dispatch.objects.filter(location=self.baru).update(tracking_number="111", location=self.baru)
        get_fos.return_value = [
            _fulfillment_order(BARU, status="CLOSED", tracking_numbers=["111"]),
            _fulfillment_order(ENVIA_LOCATION, gid="gid://shopify/FulfillmentOrder/2"),
        ]

        self.assertTrue(fulfill_in_shopify(self._dispatch(self.baru)))
        create_fulfillment.assert_not_called()

        get_fos.return_value = [_fulfillment_order(BARU, status="CLOSED", tracking_numbers=["999"])]
        Dispatch.objects.filter(location=self.baru).update(shopify_fulfilled_at=None)
        self.assertFalse(fulfill_in_shopify(self._dispatch(self.baru)))
        self.assertIn("nada abierto por cumplir en Baru", self._dispatch(self.baru).shopify_fulfillment_error)

    def test_the_listing_shows_both_dispatches(self, shipping, city):
        ensure_dispatches(self.order)

        [row] = self.client.get("/api/orders/").json()["orders"]

        self.assertEqual([(d["location_name"], d["items"]) for d in row["dispatches"]], [
            ("Baru", [{"sku": "CB8080L", "name": "Cabina", "quantity": 2}]),
            ("Bodega Envia", [{"sku": "I001", "name": "Dispensador", "quantity": 1}]),
        ])
        self.assertEqual(row["fulfillment"]["location_name"], "Baru, Bodega Envia")

    def test_buttons_reject_users_without_the_role(self, shipping, city):
        ensure_dispatches(self.order)
        self.client.force_login(User.objects.create_user("otro", password="x"))
        body = {"dispatch_id": self._dispatch(self.baru).pk}
        for path in ("notify/", "label/quote/", "label/"):
            self.assertEqual(self._post(path, body).status_code, 403)


@patch(f"{F}.label_links.BACKEND_PUBLIC_URL", "https://api.pamo.test")
class LabelLinkTests(TestCase):
    def setUp(self):
        self.dispatch = Dispatch.objects.create(
            shopify_order=_order(), location=_location(), status=Dispatch.Status.NOTIFIED,
            tracking_number="474231303", label_pdf=PDF,
        )

    def _path(self):
        link = label_links.label_link(self.dispatch)
        self.assertTrue(link.startswith("https://api.pamo.test/api/orders/labels/"))
        return link.removeprefix("https://api.pamo.test")

    def test_anyone_with_the_link_opens_that_guide(self):
        response = self.client.get(self._path())  # sin sesión

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="guia-20400-474231303.pdf"')

    def test_a_tampered_link_is_rejected(self):
        path = self._path()
        self.assertEqual(self.client.get(path[:-3] + "x/").status_code, 404)

    def test_the_link_stops_working_if_the_guide_changes(self):
        path = self._path()
        self.dispatch.label_pdf = PDF + b"otra"
        self.dispatch.save()
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_an_expired_link_says_so(self):
        path = self._path()
        with patch(f"{F}.label_links.LINK_MAX_AGE", timedelta(seconds=-1)):
            self.assertEqual(self.client.get(path).status_code, 410)

    def test_no_link_without_a_stored_guide_or_public_url(self):
        self.dispatch.label_pdf = None
        with self.assertRaisesMessage(label_links.LabelLinkError, "no tiene la guía guardada"):
            label_links.label_link(self.dispatch)
        with patch(f"{F}.label_links.BACKEND_PUBLIC_URL", ""):
            with self.assertRaisesMessage(label_links.LabelLinkError, "BACKEND_PUBLIC_URL"):
                label_links.label_link(self.dispatch)


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class DispatchListPdfTests(TestCase):
    def test_one_row_per_product_with_image_sku_and_guide_link(self):
        pdf = dispatch_list_pdf("Boccherini", [
            {"order_name": "#20400", "channel": "Mercado Libre #2000012345", "recipient": ["Ana Pérez", "Calle 1, Bogotá"],
             "tracking_number": "474231303", "label_url": "https://api.pamo.test/l/1/", "items": [
                {"sku": "I001", "name": "Dispensador ñandú", "quantity": 2, "image": _png()},
                {"sku": "", "name": "Mesa “roble”", "quantity": 1, "image": None},
            ]},
            {"order_name": "#20401", "channel": "Tienda web", "recipient": ["Luis"], "tracking_number": "", "label_url": "", "items": [
                {"sku": "S9", "name": "Silla", "quantity": 4, "image": None},
            ]},
        ])

        page = PdfReader(io.BytesIO(pdf)).pages[0]
        text = page.extract_text()
        for expected in (
            "Despachos para Boccherini", "#20400", "#2000012345", "I001", "Dispensador ñandú", "Mesa ?roble?",
            "Ana Pérez", "#20401", "Sin guía",
        ):
            self.assertIn(expected, text)
        self.assertEqual(len(page.images), 1)
        self.assertEqual([a.get_object()["/A"]["/URI"] for a in page["/Annots"]], ["https://api.pamo.test/l/1/"])


@patch(f"{F}.product_images.download_image")
@patch(f"{F}.product_images.get_variant_images_by_skus")
class ProductImagesTests(TestCase):
    def test_a_failing_image_only_leaves_that_product_without_it(self, get_urls, download):
        get_urls.return_value = {"A": "https://cdn.shopify.com/a.png", "B": "https://cdn.shopify.com/b.png", "C": "x"}
        download.side_effect = [_png(), b"no es una imagen", OSError("cdn caído")]

        self.assertEqual(product_images(["A", "B", "C"]), {"A": _png()})

    def test_without_shopify_there_are_no_images_and_no_error(self, get_urls, download):
        get_urls.side_effect = OSError("Shopify caído")
        self.assertEqual(product_images(["A"]), {})
        download.assert_not_called()


class FollowShopifyLocationsTests(TestCase):
    """Alguien cambió la bodega en Shopify: el despacho la sigue si aún no
    hizo nada; si ya tiene guía o avisos, nunca se avisa a otra bodega."""

    def setUp(self):
        self.boccherini = _location()
        self.envia = _location(ENVIA_LOCATION, "Bodega Envia")
        self.order = _order(marketplace="shopify")

    def _shopify_at(self, *location_ids):
        self.order.fulfillment_orders.all().delete()
        for position, location_id in enumerate(location_ids):
            ShopifyFulfillmentOrder.objects.create(
                order=self.order, position=position, shopify_id=f"fo-{position}", status="OPEN",
                location_id=location_id, location_name=location_id,
            )

    def _follow(self):
        from .functions.dispatch_orders import follow_shopify_locations

        return follow_shopify_locations(ShopifyOrder.objects.prefetch_related("fulfillment_orders").get(pk=self.order.pk))

    def _dispatch(self, **fields):
        fields.setdefault("status", Dispatch.Status.WAITING_LABEL)
        return Dispatch.objects.create(shopify_order=self.order, location=self.boccherini, channel="shopify", **fields)

    def test_untouched_dispatch_moves_to_the_shopify_warehouse(self):
        self._dispatch()
        self._shopify_at(ENVIA_LOCATION)

        dispatches = self._follow()

        self.assertEqual([(d.location, d.status) for d in dispatches], [(self.envia, Dispatch.Status.WAITING_LABEL)])
        self.assertEqual(Dispatch.objects.filter(shopify_order=self.order).count(), 1)

    def test_manual_dispatch_without_warehouse_takes_the_shopify_one(self):
        Dispatch.objects.create(shopify_order=self.order, channel="shopify", status=Dispatch.Status.MANUAL,
                                note="Ninguna bodega tiene el pedido completo.")
        self._shopify_at(BOCCHERINI)

        self.assertEqual([d.location for d in self._follow()], [self.boccherini])

    def test_same_warehouse_changes_nothing(self):
        dispatch = self._dispatch()
        self._shopify_at(BOCCHERINI)

        self.assertEqual([d.pk for d in self._follow()], [dispatch.pk])

    def test_dispatch_with_label_goes_manual_instead_of_moving(self):
        dispatch = self._dispatch(tracking_number="014")
        self._shopify_at(ENVIA_LOCATION)

        self._follow()

        dispatch.refresh_from_db()
        self.assertEqual((dispatch.location, dispatch.status), (self.boccherini, Dispatch.Status.MANUAL))
        self.assertIn(ENVIA_LOCATION, dispatch.note)

    def test_notified_dispatch_keeps_its_status_with_a_note(self):
        dispatch = self._dispatch(status=Dispatch.Status.NOTIFIED)
        DispatchNotification.objects.create(dispatch=dispatch, channel="email", status=DispatchNotification.Status.SENT)
        self._shopify_at(ENVIA_LOCATION)

        self._follow()

        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, Dispatch.Status.NOTIFIED)
        self.assertIn("revisar a mano", dispatch.note)

    def test_web_order_moved_into_two_warehouses_is_split(self):
        self._dispatch()
        self._shopify_at(BOCCHERINI, ENVIA_LOCATION)

        dispatches = self._follow()

        self.assertEqual([(d.location, d.split) for d in dispatches], [(self.boccherini, True), (self.envia, True)])

    def test_marketplace_order_in_two_warehouses_goes_manual(self):
        self.order.marketplace = "mercadolibre"
        self.order.save()
        self._dispatch()
        self._shopify_at(BOCCHERINI, ENVIA_LOCATION)

        dispatches = self._follow()

        self.assertEqual([(d.location, d.status) for d in dispatches], [(None, Dispatch.Status.MANUAL)])
