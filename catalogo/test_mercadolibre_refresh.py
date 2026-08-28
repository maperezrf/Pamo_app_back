import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .channel_import import import_external_channel_snapshot
from .models import ChannelSnapshot, ExternalChannelProductSnapshot, IntegrationReadStatus, MasterProduct, ProductVariant


class MercadoLibreRefreshCommandTests(TestCase):
    def test_remote_failure_preserves_previous_snapshot(self):
        import_external_channel_snapshot(
            "MERCADO_LIBRE",
            [{"external_product_id": "MCO-OLD", "sku": "OLD-1", "title": "Anterior"}],
            observed_at=timezone.now(),
            complete=True,
        )

        with patch(
            "catalogo.management.commands.refresh_mercadolibre_snapshot.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="remote read failed"),
        ):
            with self.assertRaisesMessage(CommandError, "no se modificó SQLite"):
                call_command("refresh_mercadolibre_snapshot")

        previous = ExternalChannelProductSnapshot.objects.get(external_product_id="MCO-OLD")
        self.assertTrue(previous.active)
        self.assertEqual(previous.title, "Anterior")

    def test_valid_complete_payload_is_imported_locally(self):
        product = MasterProduct.objects.create(title="Producto maestro")
        ProductVariant.objects.create(product=product, sku="ML-EXACT-1")
        payload = {
            "channel": "MERCADO_LIBRE",
            "complete": True,
            "observed_at": "2026-08-26T21:55:09Z",
            "source": "Mercado Libre API read-only QA",
            "records": [{
                "external_product_id": "MCO-NEW",
                "sku": "ML-EXACT-1",
                "title": "Publicación nueva",
                "state": "active",
                "price": 199000,
                "inventory_available": 4,
                "image_url": "https://http2.mlstatic.com/example.jpg",
                "payload": {
                    "shipping_costs": {
                        "seller_estimate": 13900,
                        "current_seller_estimate": 12900,
                        "seller_estimate_strategy": "MAX_ELIGIBLE_QUOTE_OR_HISTORICAL_P75",
                        "current_logistic_type": "cross_docking",
                        "modalities": {
                            "collecta": {
                                "eligible": True,
                                "seller_estimate": 12900,
                                "historical_p75": 12600,
                                "historical_samples": 162,
                            },
                            "flex": {
                                "eligible": True,
                                "seller_estimate": 13900,
                                "historical_p75": 8910,
                                "historical_samples": 72,
                            },
                            "full": {"eligible": False, "seller_estimate": None},
                        },
                        "buyer_charge": 0,
                    },
                    "selling_fees": {"sale_fee_amount": 27860, "percentage_fee": 14},
                    "profitability": {
                        "verified": False,
                        "commission_amount": 27860,
                        "commission_percent": 14,
                        "other_cost_amount": None,
                        "source": "Mercado Libre listing_prices",
                    },
                },
            }],
        }
        output = StringIO()

        with patch(
            "catalogo.management.commands.refresh_mercadolibre_snapshot.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ):
            call_command("refresh_mercadolibre_snapshot", stdout=output)

        snapshot = ExternalChannelProductSnapshot.objects.get(external_product_id="MCO-NEW")
        self.assertEqual(snapshot.match_status, ExternalChannelProductSnapshot.MatchStatus.EXACT_SKU)
        self.assertEqual(snapshot.image_url, "https://http2.mlstatic.com/example.jpg")
        channel_snapshot = ChannelSnapshot.objects.get(channel="MERCADO_LIBRE")
        shipping = channel_snapshot.payload["shipping_costs"]
        self.assertEqual(shipping["seller_estimate"], 13900)
        self.assertEqual(shipping["current_seller_estimate"], 12900)
        self.assertEqual(shipping["seller_estimate_strategy"], "MAX_ELIGIBLE_QUOTE_OR_HISTORICAL_P75")
        self.assertEqual(shipping["modalities"]["collecta"]["seller_estimate"], 12900)
        self.assertEqual(shipping["modalities"]["flex"]["seller_estimate"], 13900)
        self.assertEqual(shipping["modalities"]["collecta"]["historical_p75"], 12600)
        self.assertEqual(shipping["modalities"]["flex"]["historical_samples"], 72)
        self.assertEqual(channel_snapshot.payload["selling_fees"]["sale_fee_amount"], 27860)
        self.assertEqual(channel_snapshot.payload["profitability"]["commission_percent"], 14)
        status = IntegrationReadStatus.objects.get(
            system="MERCADO_LIBRE", capability="marketplace_catalog_snapshot",
        )
        self.assertEqual(status.status, IntegrationReadStatus.Status.AVAILABLE)
        self.assertEqual(status.record_count, 1)
        self.assertEqual(status.external_writes, 0)
        self.assertIn("externalWrites=0", output.getvalue())

    def test_fast_refresh_preserves_previous_commercial_costs(self):
        product = MasterProduct.objects.create(title="Producto maestro")
        ProductVariant.objects.create(product=product, sku="ML-COST-1")
        import_external_channel_snapshot(
            "MERCADO_LIBRE",
            [{
                "external_product_id": "MCO-COST",
                "sku": "ML-COST-1",
                "title": "Publicación",
                "price": 100000,
                "payload": {
                    "shipping_costs": {"seller_estimate": 8000},
                    "selling_fees": {"sale_fee_amount": 14000},
                    "profitability": {"commission_amount": 14000},
                },
            }],
            complete=True,
        )

        import_external_channel_snapshot(
            "MERCADO_LIBRE",
            [{
                "external_product_id": "MCO-COST",
                "sku": "ML-COST-1",
                "title": "Publicación actualizada",
                "price": 110000,
                "payload": {"listing_type_id": "gold_special"},
            }],
            complete=True,
        )

        snapshot = ExternalChannelProductSnapshot.objects.get(external_product_id="MCO-COST")
        self.assertEqual(snapshot.payload["shipping_costs"]["seller_estimate"], 8000)
        self.assertEqual(snapshot.payload["selling_fees"]["sale_fee_amount"], 14000)
        self.assertEqual(snapshot.payload["profitability"]["commission_amount"], 14000)
