import tempfile
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.orders.base import ExternalReadFailed
from integrations.orders.canonical import CanonicalDocument
from pedidos.management.commands.import_pamo_orders import Command
from pedidos.models import Order, Shipment, ShipmentDocument


class FakeLabelProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def shipment_document(self, canonical_id, *, prefer_manual=False):
        self.calls.append((canonical_id, prefer_manual))
        if self.error:
            raise self.error
        return self.result


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LabelCacheTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            channel="mercado-libre",
            external_id="meli-order-qa",
            visible_id="2000018144943652",
            placed_at=timezone.now(),
            customer_name="Cliente QA",
            grand_total=Decimal("100000"),
            source_snapshot={"canonicalImport": True},
        )

    def shipment(self, *, availability, remote_documents=None):
        return Shipment.objects.create(
            order=self.order,
            external_id="meli:47868842483",
            tracking_number="TRACKING-QA",
            source_snapshot={
                "canonical_shipment_id": "canonical-shipment-qa",
                "remote_documents": remote_documents or [],
                "label_availability": availability,
                "canonicalImport": True,
                "externalWrites": 0,
            },
        )

    def test_fulfillment_does_not_retry_a_pdf_that_provider_never_exposes(self):
        shipment = self.shipment(
            availability={
                "status": "not_printable",
                "reason": "MERCADOLIBRE_FULFILLMENT_NO_SELLER_LABEL",
            }
        )
        provider = FakeLabelProvider()

        counts = Command()._cache_labels(provider, [shipment], workers=1)

        self.assertEqual(provider.calls, [])
        self.assertEqual(counts["not_printable"], 1)
        self.assertFalse(ShipmentDocument.objects.filter(shipment=shipment).exists())

    def test_remote_document_is_cached_with_manual_document_fallback(self):
        shipment = self.shipment(
            availability={"status": "pending_provider", "reason": "REMOTE_READY"},
            remote_documents=[{"id": "document-qa"}],
        )
        provider = FakeLabelProvider(
            result=CanonicalDocument(
                content=b"%PDF-1.7\nlabel qa",
                mime_type="application/pdf",
                filename="guia-qa.pdf",
            )
        )

        counts = Command()._cache_labels(provider, [shipment], workers=1)

        shipment.refresh_from_db()
        self.assertEqual(provider.calls, [("canonical-shipment-qa", True)])
        self.assertEqual(counts["cached"], 1)
        self.assertTrue(ShipmentDocument.objects.filter(shipment=shipment).exists())
        self.assertEqual(
            shipment.source_snapshot["label_availability"]["status"],
            "available",
        )

    def test_provider_error_is_visible_and_keeps_existing_order(self):
        shipment = self.shipment(
            availability={"status": "pending_provider", "reason": "FETCH_SCHEDULED"}
        )
        provider = FakeLabelProvider(
            error=ExternalReadFailed("pamo_canonical", "CANONICAL_HTTP_502", 502)
        )

        counts = Command()._cache_labels(provider, [shipment], workers=1)

        shipment.refresh_from_db()
        self.assertEqual(counts["unavailable"], 1)
        self.assertEqual(
            shipment.source_snapshot["label_availability"]["status"],
            "temporary_error",
        )
        self.assertEqual(
            shipment.source_snapshot["label_availability"]["reason"],
            "CANONICAL_HTTP_502",
        )
        self.assertTrue(Order.objects.filter(id=self.order.id).exists())
