import time
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .apis import STATE_SESSION_KEY
from .client import MercadoLibreAPIError, MercadoLibreAuthError, MercadoLibreClient
from .functions.get_billing_info import get_billing_info
from .functions.get_order import get_order
from .functions.get_shipment import get_shipment
from .models import MercadoLibreToken

CREDENTIALS = {
    "integrations.mercadolibre.client.MERCADOLIBRE_CLIENT_ID": "app-id",
    "integrations.mercadolibre.client.MERCADOLIBRE_CLIENT_SECRET": "app-secret",
    "integrations.mercadolibre.client.MERCADOLIBRE_REDIRECT_URI": "https://example.com/callback",
}


def _patch_credentials(test_case):
    for target, value in CREDENTIALS.items():
        patcher = patch(target, value)
        patcher.start()
        test_case.addCleanup(patcher.stop)


def _token(access="valid-access", refresh="refresh-1", expires_in=timedelta(hours=1)):
    return MercadoLibreToken.objects.create(
        id=1,
        access_token=access,
        refresh_token=refresh,
        user_id="123",
        expires_at=timezone.now() + expires_in,
    )


def _response(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.json.return_value = payload if payload is not None else {}
    return response


def _token_payload(access="new-access", refresh="refresh-2"):
    return {"access_token": access, "refresh_token": refresh, "user_id": 123, "expires_in": 21600}


class TokenTests(TestCase):
    def setUp(self):
        _patch_credentials(self)

    @patch("integrations.mercadolibre.client.requests.post")
    def test_exchange_code_saves_the_first_token(self, mock_post):
        mock_post.return_value = _response(payload=_token_payload())
        seller_id = MercadoLibreClient().exchange_code("TG-code")
        token = MercadoLibreToken.objects.get(id=1)
        self.assertEqual(seller_id, "123")
        self.assertEqual((token.access_token, token.refresh_token), ("new-access", "refresh-2"))
        self.assertEqual(mock_post.call_args.kwargs["data"]["grant_type"], "authorization_code")

    @patch("integrations.mercadolibre.client.requests.get")
    @patch("integrations.mercadolibre.client.requests.post")
    def test_reuses_a_valid_token_without_refreshing(self, mock_post, mock_get):
        _token()
        mock_get.return_value = _response(payload={"ok": True})
        MercadoLibreClient().get("/orders/1")
        mock_post.assert_not_called()
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer valid-access")

    @patch("integrations.mercadolibre.client.requests.get")
    @patch("integrations.mercadolibre.client.requests.post")
    def test_refresh_saves_the_rotated_refresh_token(self, mock_post, mock_get):
        _token(access="old-access", expires_in=timedelta(minutes=1))
        mock_post.return_value = _response(payload=_token_payload())
        mock_get.return_value = _response(payload={"ok": True})
        MercadoLibreClient().get("/orders/1")
        token = MercadoLibreToken.objects.get(id=1)
        self.assertEqual(mock_post.call_args.kwargs["data"]["refresh_token"], "refresh-1")
        self.assertEqual((token.access_token, token.refresh_token), ("new-access", "refresh-2"))
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer new-access")

    @patch("integrations.mercadolibre.client.requests.get")
    @patch("integrations.mercadolibre.client.requests.post")
    def test_retries_once_after_a_401(self, mock_post, mock_get):
        _token(access="revoked-access")
        mock_post.return_value = _response(payload=_token_payload())
        mock_get.side_effect = [_response(status=401), _response(payload={"id": 1})]
        self.assertEqual(MercadoLibreClient().get("/orders/1"), {"id": 1})
        self.assertEqual(mock_get.call_count, 2)
        mock_post.assert_called_once()

    @patch("integrations.mercadolibre.client.requests.get")
    @patch("integrations.mercadolibre.client.requests.post")
    def test_second_401_raises_instead_of_looping(self, mock_post, mock_get):
        _token()
        mock_post.return_value = _response(payload=_token_payload())
        mock_get.return_value = _response(status=401, payload={"message": "invalid token"})
        with self.assertRaises(MercadoLibreAPIError):
            MercadoLibreClient().get("/orders/1")
        self.assertEqual(mock_get.call_count, 2)

    @patch("integrations.mercadolibre.client.requests.post")
    def test_skips_refresh_when_another_process_already_refreshed(self, mock_post):
        # Se vio vencido "stale-access", pero al tomar el bloqueo la fila
        # ya tiene otro token vigente: no se gasta el refresh token.
        _token(access="fresh-from-other-process")
        access = MercadoLibreClient()._refresh(stale_access_token="stale-access")
        self.assertEqual(access, "fresh-from-other-process")
        mock_post.assert_not_called()

    @patch("integrations.mercadolibre.client.requests.post")
    def test_invalid_grant_asks_to_reconnect(self, mock_post):
        _token(expires_in=timedelta(minutes=1))
        mock_post.return_value = _response(status=400, payload={"error": "invalid_grant"})
        with self.assertRaisesMessage(MercadoLibreAuthError, "/api/integrations/mercadolibre/connect/"):
            MercadoLibreClient().get("/orders/1")

    def test_without_a_token_row_asks_to_connect(self):
        with self.assertRaisesMessage(MercadoLibreAuthError, "/api/integrations/mercadolibre/connect/"):
            MercadoLibreClient().get("/orders/1")

    def test_missing_credentials_fail_before_calling_the_api(self):
        with patch("integrations.mercadolibre.client.MERCADOLIBRE_CLIENT_ID", ""):
            with self.assertRaises(MercadoLibreAuthError):
                MercadoLibreClient.authorization_url("state")


class ConnectFlowTests(TestCase):
    def setUp(self):
        _patch_credentials(self)
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user(username="admin@pamo.test")
        self.admin.groups.add(admin_group)
        self.connect_url = reverse("mercadolibre-connect")
        self.callback_url = reverse("mercadolibre-callback")

    def _start(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.connect_url)
        return parse_qs(urlparse(response["Location"]).query)["state"][0]

    def test_admin_is_redirected_to_mercadolibre_with_a_state(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.connect_url)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["client_id"], ["app-id"])
        self.assertEqual(query["state"], [self.client.session[STATE_SESSION_KEY]["value"]])

    def test_connect_rejects_user_without_admin_role(self):
        self.client.force_login(User.objects.create_user(username="otro@pamo.test"))
        self.assertEqual(self.client.get(self.connect_url).status_code, 403)

    def test_connect_rejects_anonymous_user(self):
        self.assertEqual(self.client.get(self.connect_url).status_code, 403)

    def test_connect_without_credentials_is_503(self):
        self.client.force_login(self.admin)
        with patch("integrations.mercadolibre.client.MERCADOLIBRE_CLIENT_ID", ""):
            self.assertEqual(self.client.get(self.connect_url).status_code, 503)

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_with_valid_state_saves_the_token_without_returning_it(self, mock_post):
        mock_post.return_value = _response(payload=_token_payload(access="secret-access", refresh="secret-refresh"))
        state = self._start()
        response = self.client.get(self.callback_url, {"code": "TG-code", "state": state})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"connected": True, "seller_id": "123"})
        self.assertEqual(MercadoLibreToken.objects.get(id=1).refresh_token, "secret-refresh")
        self.assertNotIn(STATE_SESSION_KEY, self.client.session)

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_with_wrong_state_does_not_exchange(self, mock_post):
        self._start()
        response = self.client.get(self.callback_url, {"code": "TG-code", "state": "forged"})
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_state_cannot_be_reused(self, mock_post):
        mock_post.return_value = _response(payload=_token_payload())
        state = self._start()
        self.client.get(self.callback_url, {"code": "TG-code", "state": state})
        response = self.client.get(self.callback_url, {"code": "TG-code", "state": state})
        self.assertEqual(response.status_code, 400)
        mock_post.assert_called_once()

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_with_expired_state_does_not_exchange(self, mock_post):
        state = self._start()
        session = self.client.session
        session[STATE_SESSION_KEY] = {"value": state, "created_at": int(time.time()) - 3600}
        session.save()
        response = self.client.get(self.callback_url, {"code": "TG-code", "state": state})
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_when_seller_denies_access(self, mock_post):
        state = self._start()
        response = self.client.get(self.callback_url, {"error": "access_denied", "state": state})
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("integrations.mercadolibre.client.requests.post")
    def test_callback_with_rejected_code_is_400(self, mock_post):
        mock_post.return_value = _response(status=400, payload={"error": "invalid_grant"})
        state = self._start()
        response = self.client.get(self.callback_url, {"code": "expired", "state": state})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MercadoLibreToken.objects.exists())

    def test_callback_rejects_user_without_admin_role(self):
        self.client.force_login(User.objects.create_user(username="otro@pamo.test"))
        self.assertEqual(self.client.get(self.callback_url, {"code": "x", "state": "y"}).status_code, 403)


@patch("integrations.mercadolibre.client.MercadoLibreClient.get")
class ReadFunctionTests(TestCase):
    def test_get_order_normalizes_items_and_keeps_empty_sku(self, mock_get):
        mock_get.return_value = {
            "id": 2000001,
            "status": "paid",
            "date_created": "2026-09-23T10:00:00.000-04:00",
            "pack_id": None,
            "shipping": {"id": 4400001},
            "buyer": {"id": 99, "nickname": "COMPRADOR"},
            "order_items": [
                {"item": {"id": "MCO1", "title": "Taladro", "seller_sku": "SKU-1", "variation_id": 7}, "quantity": 2, "unit_price": 150000},
                {"item": {"id": "MCO2", "title": "Broca", "seller_sku": None}, "quantity": 1, "unit_price": 9900.5},
            ],
        }
        order = get_order("2000001")
        mock_get.assert_called_once_with("/orders/2000001")
        self.assertEqual(order["order_id"], "2000001")
        self.assertEqual(order["pack_id"], "")
        self.assertEqual(order["shipment_id"], "4400001")
        self.assertEqual(order["buyer"], {"id": "99", "nickname": "COMPRADOR"})
        self.assertEqual(order["items"][0]["sku"], "SKU-1")
        self.assertEqual(order["items"][0]["variation_id"], "7")
        self.assertEqual(order["items"][0]["quantity"], 2)
        self.assertEqual(order["items"][1]["sku"], "")  # nunca el item.id
        self.assertEqual(order["items"][1]["price"], "9900.5")

    def test_get_billing_info_maps_additional_info(self, mock_get):
        mock_get.return_value = {
            "billing_info": {
                "doc_type": "CC",
                "doc_number": "1000000000",
                "additional_info": [
                    {"type": "FIRST_NAME", "value": "Ana"},
                    {"type": "LAST_NAME", "value": "Pérez"},
                    {"type": "STREET_NAME", "value": "Calle 1"},
                    {"type": "STREET_NUMBER", "value": "2-3"},
                    {"type": "CITY_NAME", "value": "Bogotá"},
                    {"type": "STATE_NAME", "value": "Cundinamarca"},
                ],
            }
        }
        billing = get_billing_info("2000001")
        mock_get.assert_called_once_with("/orders/2000001/billing_info")
        self.assertEqual(billing["customer_identification_type"], "CC")
        self.assertEqual(billing["customer_identification"], "1000000000")
        self.assertEqual(billing["customer_first_name"], "Ana")
        self.assertEqual(billing["customer_address"], "Calle 1 2-3")
        self.assertEqual(billing["customer_city"], "Bogotá")
        self.assertEqual(billing["customer_region"], "Cundinamarca")

    def test_get_billing_info_with_missing_data_returns_empty_strings(self, mock_get):
        mock_get.return_value = {}
        billing = get_billing_info("2000001")
        self.assertEqual(billing["customer_identification"], "")
        self.assertEqual(billing["customer_address"], "")

    def test_get_shipment_reads_logistic_type_and_receiver(self, mock_get):
        mock_get.return_value = {
            "id": 4400001,
            "status": "ready_to_ship",
            "logistic_type": "fulfillment",
            "receiver_address": {
                "receiver_name": "Ana Pérez",
                "receiver_phone": "XXXXXXX",
                "address_line": "Calle 1 # 2-3",
                "city": {"name": "Bogotá"},
                "state": {"name": "Cundinamarca"},
            },
        }
        shipment = get_shipment("4400001")
        mock_get.assert_called_once_with("/shipments/4400001")
        self.assertEqual(shipment["logistic_type"], "fulfillment")
        self.assertEqual(shipment["receiver"]["city"], "Bogotá")
        self.assertEqual(shipment["receiver"]["phone"], "XXXXXXX")

    def test_get_shipment_falls_back_to_new_format_logistic(self, mock_get):
        mock_get.return_value = {"id": 1, "logistic": {"type": "xd_drop_off"}}
        self.assertEqual(get_shipment("1")["logistic_type"], "xd_drop_off")
