from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from .functions.assign_dispatch_location import assign_dispatch_location
from .functions.dispatch_orders import dispatch_orders
from .functions.sync_dispatch_locations import sync_dispatch_locations
from .models import Dispatch, DispatchLocation, DispatchNotification, MarketplaceOrder, ShopifyOrder, ShopifyOrderLine

F = "orders.functions"
PDF = b"%PDF-1.4 " + b"x" * 64 + b" %%EOF"
ENVIA_LOCATION = "97615380757"
BOCCHERINI = "99576545557"
EMAIL_SETTINGS = {"EMAIL_HOST": "smtp.test", "DEFAULT_FROM_EMAIL": "despachos@pamo.co"}


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


@patch(f"{F}.dispatch_orders.DISPATCH_START_DATE", "2026-10-08")
class DispatchOrdersTests(TestCase):
    def setUp(self):
        self.location = _location(notify_email=True, emails=["bodega@boccherini.com"])
        self.order = _order()
        _marketplace_row(self.order)

    def _dispatch(self):
        return Dispatch.objects.get(shopify_order=self.order)

    def test_without_a_start_date_nothing_enters_the_flow(self):
        with patch(f"{F}.dispatch_orders.DISPATCH_START_DATE", ""):
            self.assertEqual(dispatch_orders(), {})
        self.assertFalse(Dispatch.objects.exists())

    def test_old_and_fulfilled_orders_are_left_out(self):
        _order(shopify_id="old", created=datetime(2026, 10, 7, 4, 59, tzinfo=dt_timezone.utc))  # 6 oct en Colombia
        _order(shopify_id="done", fulfillment_status="FULFILLED")
        dispatch_orders()
        self.assertEqual(list(Dispatch.objects.values_list("shopify_order__shopify_id", flat=True)), ["5001"])

    def test_waits_for_the_guide_while_label_download_is_off(self):
        self.assertEqual(dispatch_orders(), {"esperando_guia": 1})
        self.assertEqual(self._dispatch().location, self.location)
        self.assertIn("DISPATCH_FETCH_LABELS_ENABLED", self._dispatch().note)

    @patch(f"{F}.fetch_dispatch_label.DISPATCH_FETCH_LABELS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value=ML_SHIPMENT)
    def test_with_the_guide_but_notifications_off_it_is_ready_and_sends_nothing(self, get_shipment, get_label):
        self.assertEqual(dispatch_orders(), {"listo": 1})

        dispatch = self._dispatch()
        self.assertEqual(dispatch.tracking_number, "MEL48195006881")
        self.assertEqual(len(dispatch.label_sha256), 64)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(DispatchNotification.objects.exists())

    @patch(f"{F}.fetch_dispatch_label.DISPATCH_FETCH_LABELS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value={**ML_SHIPMENT, "substatus": "in_packing_list", "status": "shipped"})
    def test_mercadolibre_guide_not_available_yet_waits(self, get_shipment):
        self.assertEqual(dispatch_orders(), {"esperando_guia": 1})
        self.assertIn("aún no disponible", self._dispatch().note)

    @override_settings(**EMAIL_SETTINGS)
    @patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.DISPATCH_FETCH_LABELS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value=ML_SHIPMENT)
    def test_emails_the_warehouse_once_with_the_guide(self, get_shipment, get_label):
        self.assertEqual(dispatch_orders(), {"notificado": 1})

        [message] = mail.outbox
        self.assertEqual(message.to, ["bodega@boccherini.com"])
        self.assertIn("20400", message.subject)
        self.assertIn("I001 x1", message.body)
        self.assertIn("MEL48195006881", message.body)
        self.assertEqual(message.attachments[0][1], PDF)
        notification = DispatchNotification.objects.get()
        self.assertEqual((notification.channel, notification.status), ("email", "enviado"))

        dispatch_orders()  # ya notificado: no vuelve a enviar
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="")  # sin depender del .env
    @patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
    def test_unconfigured_email_whatsapp_and_unknown_envia_sku_are_errors_not_sends(self):
        self.location.requires_label = False
        self.location.notify_whatsapp = True
        self.location.whatsapp_numbers = ["573001234567"]
        self.location.notify_api = True
        self.location.envia_warehouse_id = "311"
        self.location.save()

        with patch(f"{F}.notify_dispatch.create_order") as create_order, \
                patch(f"{F}.notify_dispatch.send_template_message") as send_template, \
                patch(f"{F}.notify_dispatch.find_order", return_value=None), \
                patch(f"{F}.notify_dispatch.find_variant_ids", return_value={}):
            self.assertEqual(dispatch_orders(), {"error": 1})
            create_order.assert_not_called()
            send_template.assert_not_called()

        errors = dict(DispatchNotification.objects.values_list("channel", "error_description"))
        self.assertIn("EMAIL_HOST", errors["email"])
        self.assertIn("DISPATCH_WHATSAPP_TEMPLATE", errors["whatsapp"])
        self.assertIn("SKU sin producto en Envía: I001", errors["api"])
        self.assertEqual(len(mail.outbox), 0)

    @patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
    @patch(f"{F}.notify_dispatch.DISPATCH_WHATSAPP_TEMPLATE", "despacho_bodega")
    @patch(f"{F}.notify_dispatch.send_template_message", return_value={"message_id": "wamid.1"})
    @patch(f"{F}.notify_dispatch.upload_document")
    def test_whatsapp_uses_the_approved_template(self, upload, send_template):
        self.location.requires_label = False
        self.location.notify_email = False
        self.location.notify_whatsapp = True
        self.location.whatsapp_numbers = ["573001234567"]
        self.location.save()

        self.assertEqual(dispatch_orders(), {"notificado": 1})

        upload.assert_not_called()  # sin guía, sin documento
        args, kwargs = send_template.call_args
        self.assertEqual(args, ("573001234567", "despacho_bodega"))
        self.assertEqual(kwargs["body_params"], ["20400", "I001 x1", "Bogotá"])
        self.assertEqual(DispatchNotification.objects.get().external_id, "wamid.1")

    @override_settings(**EMAIL_SETTINGS)
    @patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
    def test_a_failed_notification_is_retried_and_a_processing_one_is_not(self):
        self.location.requires_label = False
        self.location.save()
        with patch(f"{F}.notify_dispatch.EmailMessage.send", side_effect=OSError("smtp caído")):
            self.assertEqual(dispatch_orders(), {"error": 1})

        self.assertEqual(dispatch_orders(), {"notificado": 1})  # reintento
        self.assertEqual(len(mail.outbox), 1)

        other = _order(shopify_id="5002", name="20401")
        _marketplace_row(other, marketplace_order_id="mk-2")
        dispatch = Dispatch.objects.create(shopify_order=other, location=self.location, channel="mercadolibre", status="error")
        DispatchNotification.objects.create(dispatch=dispatch, channel="email", status="procesando")
        dispatch_orders()
        self.assertEqual(len(mail.outbox), 1)  # el que quedó procesando no se reenvía

    def test_location_without_channels_goes_to_manual(self):
        self.location.notify_email = False
        self.location.save()
        self.assertEqual(dispatch_orders(), {"manual": 1})
        self.assertIn("no tiene canales", self._dispatch().note)

    def test_cancelled_order_gets_a_cancelled_dispatch_and_leaves_the_flow(self):
        self.order.cancelled_at = self.order.shopify_created_at
        self.order.save()
        self.assertEqual(dispatch_orders(), {"cancelado": 1})
        self.assertEqual(dispatch_orders(), {})

    @patch(f"{F}.fetch_dispatch_label.DISPATCH_FETCH_LABELS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.get_shipping_document", return_value=PDF)
    @patch(f"{F}.fetch_dispatch_label.get_package_items")
    def test_falabella_guide_comes_from_the_package_items(self, package_items, get_document):
        order = _order(shopify_id="6001", name="20402", marketplace="falabella")
        _marketplace_row(order, marketplace="falabella", marketplace_order_id="8001737501", shipment_id="")
        package_items.return_value = [
            {"order_item_id": "11", "package_id": "PKG1", "tracking_code": "2298450467", "status": "ready_to_ship"},
            {"order_item_id": "12", "package_id": "", "tracking_code": "", "status": "pending"},
        ]

        dispatch_orders(params={"shopify_order_ids": ["6001"]})

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

        self.assertEqual(orders["20400"]["dispatch"]["location_name"], "Boccherini")
        self.assertEqual(orders["20400"]["dispatch"]["notifications"][0]["channel"], "email")
        self.assertIsNone(orders["20401"]["dispatch"])


class EnviaIdentifierTests(TestCase):
    def test_is_the_bare_channel_number_or_the_shopify_number(self):
        from .functions.notify_dispatch import _envia_identifier

        # Operaciones pidió ver solo el número (2026-10-08), sin "sodimac-".
        self.assertEqual(_envia_identifier({"marketplace_numbers": ["16200704"], "order_name": "20400"}), "16200704")
        self.assertEqual(_envia_identifier({"marketplace_numbers": [], "order_name": "#20412"}), "20412")


N = f"{F}.notify_dispatch"
ENVIA_EXISTING = {"order_id": 49607578, "identifier": "20400", "warehouse_id": 311, "warehouse_status": "CREATED",
                  "fulfillment_status": "unfulfilled", "shop_name": "Pamo.co", "pretrackings": []}


@patch(f"{F}.dispatch_orders.DISPATCH_START_DATE", "2026-10-08")
@patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
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
        # Sodimac es "manual" en la asignación automática; aquí se prueba el
        # notificador con el despacho ya asignado.
        self.dispatch = Dispatch.objects.create(
            shopify_order=self.order, location=self.location, channel="sodimac", status=Dispatch.Status.WAITING_LABEL
        )

    @patch(f"{N}.add_order_tracking")
    @patch(f"{N}.create_order")
    @patch(f"{N}.find_order", return_value=ENVIA_EXISTING)
    def test_links_the_order_envia_already_has_instead_of_creating_another(self, find_order, create_order, add_tracking):
        dispatch_orders()

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
        dispatch_orders()

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
            dispatch_orders()
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

        response = self._post(order, "label/quote/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["options"][0]["carrier"], "tcc")
        self.assertNotIn("providerPayload", response.json()["options"][0])
        payload = quote.call_args.args[0]
        self.assertEqual(payload["origin"]["street"], "Calle 1")
        self.assertEqual(payload["destination"]["street"], "Calle 9")
        self.assertEqual(payload["packages"][0]["weight"], 1)

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
        self.assertEqual(self._post(order, "label/", body).status_code, 400)
        self.assertEqual(create_label.call_count, 1)

    @patch(f"{A}._shipping_payload", return_value={})
    def test_generate_with_writes_disabled_is_409_and_unknown_result_is_502(self, payload):
        from integrations.envia.functions.create_label import LabelUnknownResult, LabelWritesDisabled

        order = self._web_order()
        body = {"carrier": "tcc", "service": "mensajeria"}
        with patch(f"{A}.create_label", side_effect=LabelWritesDisabled()):
            self.assertEqual(self._post(order, "label/", body).status_code, 409)
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

        dispatch = assign_web_dispatch(order.shopify_id)

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


@patch(f"{F}.dispatch_orders.DISPATCH_START_DATE", "2026-10-08")
@patch(f"{F}.dispatch_orders.DISPATCH_NOTIFICATIONS_ENABLED", True)
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

        self.assertEqual(dispatch_orders(), {"notificado": 1})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments, [])

    def test_otherwise_the_web_order_waits_for_a_generated_label(self):
        self._dispatch(creates_own_label=False)

        self.assertEqual(dispatch_orders(), {"esperando_guia": 1})
        self.assertEqual(len(mail.outbox), 0)

    @patch(f"{F}.fetch_dispatch_label.DISPATCH_FETCH_LABELS_ENABLED", True)
    @patch(f"{F}.fetch_dispatch_label.get_shipment", return_value={**ML_SHIPMENT, "substatus": "ready_to_print"})
    @patch(f"{F}.fetch_dispatch_label.get_shipment_label", return_value=PDF)
    @override_settings(**EMAIL_SETTINGS)
    def test_mercadolibre_still_carries_its_channel_label(self, get_label, get_shipment):
        place = _location(shopify_location_id="777", name="Envia ML", notify_email=True, emails=["b@example.com"],
                          creates_own_label=True)
        order = _order(shopify_id="9001", name="20500")
        _marketplace_row(order, location_id="777", marketplace_order_id="ml-1")
        Dispatch.objects.create(shopify_order=order, location=place, channel="mercadolibre", status=Dispatch.Status.WAITING_LABEL)

        dispatch_orders(params={"shopify_order_ids": ["9001"]})

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

    @patch(f"{F}.send_test_notification.send_template_message", return_value={"message_id": "wamid.1", "raw": {}})
    def test_a_superuser_sends_a_test_whatsapp(self, send):
        self._login(is_superuser=True)

        response = self.client.post(self.URL, {"channel": "whatsapp", "to": "573001234567"}, follow=True)

        self.assertContains(response, "WhatsApp enviado a 573001234567")
        send.assert_called_once_with("573001234567", "hello_world", language="en_US", body_params=())

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="")
    def test_a_missing_email_setup_is_shown_as_an_error(self):
        self._login(is_superuser=True)

        response = self.client.post(self.URL, {"channel": "email", "to": "yo@example.com"}, follow=True)

        self.assertContains(response, "Falta EMAIL_HOST")
        self.assertEqual(mail.outbox, [])

    @override_settings(**EMAIL_SETTINGS)
    def test_staff_without_superuser_is_refused(self):
        self._login()

        self.assertEqual(self.client.post(self.URL, {"channel": "email", "to": "yo@example.com"}).status_code, 403)
        self.assertEqual(mail.outbox, [])
