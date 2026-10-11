from unittest.mock import MagicMock, patch

import requests

from django.test import SimpleTestCase

from .client import SodimacRpaBusy, SodimacRpaError
from .functions.trigger_labels import trigger_labels

C = "integrations.sodimac_rpa.client"


def _response(status, body=None, text=""):
    response = MagicMock(status_code=status, text=text)
    response.json.return_value = body
    return response


@patch(f"{C}.SODIMAC_RPA_URL", "https://rpa.example/")
class TriggerLabelsTests(SimpleTestCase):
    @patch(f"{C}.requests.request", return_value=_response(200, {"started": True, "ordenes": ["1"]}))
    def test_posts_the_purchase_orders_to_trigger(self, post):
        self.assertEqual(trigger_labels([1]), {"started": True, "ordenes": ["1"]})
        self.assertEqual(post.call_args.args, ("POST", "https://rpa.example/trigger"))
        self.assertEqual(post.call_args.kwargs["json"], {"ordenes": ["1"]})

    @patch(f"{C}.requests.request", return_value=_response(409, text="Ya hay una ejecución en curso"))
    def test_a_running_rpa_is_busy(self, post):
        with self.assertRaises(SodimacRpaBusy):
            trigger_labels(["1"])

    @patch(f"{C}.requests.request", return_value=_response(500, text="boom"))
    def test_other_errors_raise(self, post):
        with self.assertRaisesMessage(SodimacRpaError, "HTTP 500"):
            trigger_labels(["1"])

    @patch(f"{C}.requests.request", side_effect=requests.ConnectionError("refused"))
    def test_an_unreachable_rpa_is_a_readable_error(self, post):
        with self.assertRaisesMessage(SodimacRpaError, "No se pudo conectar con el RPA en https://rpa.example"):
            trigger_labels(["1"])


class NotConfiguredTests(SimpleTestCase):
    @patch(f"{C}.SODIMAC_RPA_URL", "")
    def test_without_url_it_fails_before_calling(self):
        with self.assertRaisesMessage(SodimacRpaError, "SODIMAC_RPA_URL"):
            trigger_labels(["1"])
