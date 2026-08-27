import json
import sys
from decimal import Decimal
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.dateparse import parse_datetime

from catalogo.models import PhysicalEvidenceCandidate, ProductVariant, SkuReconciliation
from catalogo.physical import PhysicalValidationError, upsert_candidate


class Command(BaseCommand):
    help = "Importa evidencia pública sanitizada; no navega ni escribe sistemas externos."

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo persiste en SQLite local.")
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as error:
            raise CommandError(f"JSON inválido: {error}") from error
        imported = rejected = 0
        for row in payload.get("records") or []:
            try:
                sku = str(row.get("sku") or "").strip()
                variants = list(ProductVariant.objects.filter(sku=sku)[:2])
                if len(variants) != 1:
                    raise ValueError("SKU local no es único")
                variant = variants[0]
                reconciliation = SkuReconciliation.objects.filter(variant=variant, status="EXACT").select_related("supplier_item").first()
                if not reconciliation:
                    raise ValueError("SKU no está conciliado exactamente con proveedor")
                source_type = str(row.get("source_type") or "")
                if source_type not in {"MANUFACTURER", "PUBLIC_RETAIL_EXACT", "PUBLIC_SIMILAR"}:
                    raise ValueError("source_type público no permitido")
                url = str(row.get("source_url") or "")
                if urlparse(url).scheme not in {"http", "https"}:
                    raise ValueError("URL pública inválida")
                identifier_type = str(row.get("identifier_type") or "").upper()
                identifier_value = str(row.get("identifier_value") or "").strip()
                exact_values = {variant.sku, variant.barcode}
                exact_match = identifier_type in {"SKU", "GTIN", "EAN", "UPC", "MPN", "MODEL"} and identifier_value in exact_values
                if source_type != "PUBLIC_SIMILAR" and not exact_match:
                    raise ValueError("fuente exacta no coincide con SKU/GTIN local")
                classification = "ESTIMATED" if source_type == "PUBLIC_SIMILAR" else "DERIVED"
                scope = str(row.get("scope") or "PRODUCT").upper()
                if source_type == "PUBLIC_SIMILAR":
                    scope = "PRODUCT"
                if scope not in {"PRODUCT", "PACKAGE"}:
                    raise ValueError("scope inválido")
                upsert_candidate(
                    variant=variant, supplier_item=reconciliation.supplier_item,
                    field=str(row.get("field") or "").upper(), scope=scope,
                    classification=classification, source_type=source_type,
                    source_reference=str(row.get("source_reference") or "Fuente pública")[:500],
                    source_url=url, evidence_excerpt=str(row.get("evidence_excerpt") or "")[:700],
                    selector=str(row.get("selector") or "")[:300], original_value=row.get("value"),
                    original_unit=row.get("unit"), confidence=Decimal(str(row.get("confidence") or ("0.65" if exact_match else "0.35"))),
                    identifier_type=identifier_type, identifier_value=identifier_value,
                    extraction_method="PUBLIC_EXACT_V1" if exact_match else "PUBLIC_SIMILAR_ESTIMATE_V1",
                    observed_at=parse_datetime(row.get("observed_at") or ""),
                )
                imported += 1
            except (ValueError, PhysicalValidationError, TypeError):
                rejected += 1
        self.stdout.write(self.style.SUCCESS(
            f"Evidencia pública local: {imported} importadas, {rejected} rechazadas; externalWrites=0."
        ))
