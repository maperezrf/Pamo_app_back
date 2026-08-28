from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from .models import IntegrationReadStatus, MasterProduct, ProductVariant


class EnviaProductQuoteRefreshTests(TestCase):
    def setUp(self):
        product = MasterProduct.objects.create(title="Producto sin paquete")
        ProductVariant.objects.create(product=product, sku="NO-PACKAGE-1")

    def test_execute_with_no_eligible_package_makes_no_remote_request(self):
        output = StringIO()
        call_command("refresh_envia_product_quotes", "--execute-read", stdout=output)
        status = IntegrationReadStatus.objects.get(system="ENVIA", capability="product_rate_quote")
        self.assertEqual(status.status, IntegrationReadStatus.Status.BLOCKED)
        self.assertEqual(status.details["eligible"], 0)
        self.assertIn("0 elegibles", output.getvalue())

    def test_remote_script_is_rate_only_and_uses_shipping_token(self):
        script = (Path(settings.BASE_DIR) / "catalogo" / "scripts" / "export_envia_rates.mjs").read_text()
        self.assertIn("ENVIA_SHIPPING_API_TOKEN", script)
        self.assertIn("/ship/rate/", script)
        self.assertNotIn("ENVIA_API_TOKEN", script)
        self.assertNotIn("/ship/generate", script)
