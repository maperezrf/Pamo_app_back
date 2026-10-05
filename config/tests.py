import requests
from django.test import SimpleTestCase

from .test_runner import RealNetworkCallError


class NoNetworkTestRunnerTests(SimpleTestCase):
    def test_real_http_calls_are_blocked(self):
        with self.assertRaises(RealNetworkCallError):
            requests.get("https://example.com", timeout=5)
