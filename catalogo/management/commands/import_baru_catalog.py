from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from catalogo.baru_pdf import BaruCatalogError, derive_net_cost, extract_baru_catalog
from catalogo.models import (
    CanonicalCostSelection,
    CatalogHistoryEvent,
    CostObservation,
    InventorySourceSnapshot,
    MasterProduct,
    ProductVariant,
    ProviderConfig,
    SkuReconciliation,
    SupplierCatalogImport,
    SupplierCatalogItem,
)


class Command(BaseCommand):
    help = "Importa el PDF Barú original únicamente a SQLite local, con evidencia e IVA incluido."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", required=True)
        parser.add_argument("--catalog-date", required=True)
        parser.add_argument("--tax-rate", default="19")
        parser.add_argument("--expected-pages", type=int, default=91)

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede escribir en SQLite local.")
        try:
            catalog_date = date.fromisoformat(options["catalog_date"])
            tax_rate = Decimal(str(options["tax_rate"]))
            rows, audit = extract_baru_catalog(options["pdf"])
        except (ValueError, BaruCatalogError) as error:
            raise CommandError(str(error)) from error

        if audit["page_count"] != options["expected_pages"]:
            raise CommandError(f"Se esperaban {options['expected_pages']} páginas y se encontraron {audit['page_count']}.")
        blocking = {
            "duplicate_skus": len(audit["duplicate_skus"]),
            "invalid_prices": len(audit["invalid_prices"]),
            "missing_skus": len(audit["missing_skus"]),
            "missing_descriptions": len(audit["missing_descriptions"]),
        }
        if any(blocking.values()):
            raise CommandError(f"El PDF no supera la validación previa: {blocking}")

        # Retira exclusivamente el fixture Barú anterior; conserva los fixtures QA.
        fake_product_ids = list(ProductVariant.objects.filter(sku__startswith="BARU-DEMO-").values_list("product_id", flat=True))
        MasterProduct.objects.filter(pk__in=fake_product_ids).delete()
        SupplierCatalogItem.objects.filter(source_batch="LOCAL-FIXTURE-001").delete()
        ProviderConfig.objects.filter(name="Barú · demostración local").delete()

        provider, _ = ProviderConfig.objects.update_or_create(
            name="Barú",
            defaults={
                "source_reference": f"{audit['source_filename']} · SHA-256 {audit['source_sha256']}",
                "currency": "COP",
                "tax_treatment": ProviderConfig.TaxTreatment.INCLUDED,
                "tax_rate": tax_rate,
                "general_discount_percent": 0,
                "charge_percent": 0,
                "fixed_charge": 0,
                "valid_from": None,
                "valid_until": None,
                "notes": (
                    "Mauricio confirmó que todos los precios del catálogo incluyen IVA. "
                    "El precio bruto original se conserva sin sumar IVA. El neto es solo un derivado auditable. "
                    "El PDF no informa descuentos, inventario, peso, dimensiones, bodega ni vigencia."
                ),
            },
        )
        batch_key = f"BARU-{catalog_date.isoformat()}-{audit['source_sha256'][:12]}"
        sku_counts = Counter(row.sku for row in rows)
        observed_at = timezone.now()
        exact = missing = ambiguous = duplicates = 0

        for row in rows:
            gross_price = row.gross_price
            net_cost = derive_net_cost(gross_price, tax_rate)
            item, _ = SupplierCatalogItem.objects.update_or_create(
                provider=provider,
                source_batch=batch_key,
                supplier_sku=row.sku,
                defaults={
                    "source_row": f"page:{row.page};row:{row.row_on_page}",
                    "supplier_code": row.sku,
                    "description": row.description,
                    "supplier_price": gross_price,
                    "derived_net_cost": net_cost,
                    "inventory": None,
                    "weight_kg": None,
                    "dimensions": {},
                    "warehouse": "",
                    "valid_from": None,
                    "valid_until": None,
                    "missing_fields": ["image_asset", "inventory", "weight", "dimensions", "warehouse", "validity"],
                    "raw_payload": {
                        "source_filename": audit["source_filename"],
                        "source_sha256": audit["source_sha256"],
                        "source_page": row.page,
                        "source_row_on_page": row.row_on_page,
                        "raw_price": row.raw_price,
                        "gross_price_includes_tax": True,
                        "tax_rate": str(tax_rate),
                        "derived_net_cost": str(net_cost),
                        "derivation": "gross_price / (1 + tax_rate / 100)",
                        "discount_applied": False,
                        "discount_evidence": "NOT_PROVIDED_BY_PDF",
                    },
                },
            )
            SkuReconciliation.objects.filter(supplier_item=item).delete()
            shopify_candidates = list(ProductVariant.objects.exclude(shopify_variant_id="").filter(sku=row.sku)[:3])

            if sku_counts[row.sku] > 1:
                status = SkuReconciliation.Status.DUPLICATE
                reason = "El SKU aparece más de una vez en el PDF Barú; requiere revisión."
                variant = None
                duplicates += 1
            elif len(shopify_candidates) > 1:
                status = SkuReconciliation.Status.AMBIGUOUS
                reason = "Más de una variante Shopify local usa el mismo SKU exacto."
                variant = None
                ambiguous += 1
            elif len(shopify_candidates) == 1:
                status = SkuReconciliation.Status.EXACT
                reason = "Coincidencia exacta entre el SKU del PDF Barú y Shopify local."
                variant = shopify_candidates[0]
                exact += 1
            else:
                status = SkuReconciliation.Status.MISSING
                reason = "SKU del catálogo Barú ausente en el snapshot Shopify; queda catálogo-only."
                variant = ProductVariant.objects.filter(
                    sku=row.sku, shopify_variant_id="", product__status="SUPPLIER_ONLY",
                ).first()
                if not variant:
                    product = MasterProduct.objects.create(
                        title=row.description[:300], vendor="BARU", brand="BARU",
                        status="SUPPLIER_ONLY", tags=["supplier-catalog", "baru"], collections=[],
                        quality_score=45,
                        missing_fields=["image_asset", "selling_price", "inventory", "weight", "dimensions", "warehouse", "validity"],
                        needs_review=True,
                    )
                    variant = ProductVariant.objects.create(product=product, sku=row.sku, title="Catálogo de proveedor")
                missing += 1

            SkuReconciliation.objects.create(
                supplier_item=item,
                variant=variant,
                status=status,
                candidate_variant_ids=[str(candidate.id) for candidate in shopify_candidates],
                reason=reason,
            )
            if not variant:
                continue

            variant.provider_cost = gross_price
            variant.save(update_fields=["provider_cost"])
            evidence = f"{audit['source_filename']} · page {row.page} · SHA-256 {audit['source_sha256']}"
            observation, _ = CostObservation.objects.update_or_create(
                variant=variant,
                source=CostObservation.Source.PROVIDER_CATALOG,
                provider=provider,
                evidence_reference=evidence,
                defaults={
                    "raw_cost": gross_price,
                    "derived_net_cost": net_cost,
                    "currency": "COP",
                    "tax_treatment": ProviderConfig.TaxTreatment.INCLUDED,
                    "tax_rate": tax_rate,
                    "discount_percent": 0,
                    "observed_at": observed_at,
                    "valid_from": None,
                    "valid_until": None,
                    "payload_fingerprint": audit["source_sha256"],
                },
            )
            CanonicalCostSelection.objects.update_or_create(
                variant=variant,
                defaults={
                    "observation": observation,
                    "policy_name": "Catálogo Barú aprobado · precio bruto con IVA incluido",
                    "reason": (
                        "Se conserva el precio bruto confirmado por Mauricio como costo canónico. "
                        "No se agrega IVA nuevamente; el neto mostrado es informativo y derivado con la tasa configurable."
                    ),
                    "discrepancy": {"tax_guard": "DO_NOT_ADD_TAX", "net_cost_is_derived_only": True},
                },
            )
            InventorySourceSnapshot.objects.update_or_create(
                variant=variant,
                provider=provider,
                source_name="Barú",
                warehouse_external_id="PENDING",
                defaults={
                    "warehouse_name": "Bodega pendiente",
                    "reported_stock": None,
                    "reserved_stock": 0,
                    "safety_stock": 0,
                    "available_to_promise": None,
                    "stock_unknown": True,
                    "observed_at": observed_at,
                    "freshness_minutes": 1440,
                    "update_method": InventorySourceSnapshot.UpdateMethod.FILE,
                    "canonical": False,
                    "evidence_reference": "El PDF Barú no contiene inventario ni bodega.",
                },
            )

        sample_indexes = [0, 1, 6, len(rows) // 2, len(rows) - 1]
        sample_rows = [{
            "page": rows[index].page,
            "sku": rows[index].sku,
            "raw_price": rows[index].raw_price,
            "gross_price": str(rows[index].gross_price),
        } for index in sample_indexes]
        import_record, _ = SupplierCatalogImport.objects.update_or_create(
            source_sha256=audit["source_sha256"],
            defaults={
                "provider": provider,
                "source_filename": audit["source_filename"],
                "source_path_at_import": str(Path(options["pdf"]).resolve()),
                "catalog_date": catalog_date,
                "page_count": audit["page_count"],
                "extracted_rows": audit["extracted_rows"],
                "unique_skus": audit["unique_skus"],
                "duplicate_skus": len(audit["duplicate_skus"]),
                "invalid_prices": len(audit["invalid_prices"]),
                "missing_skus": len(audit["missing_skus"]),
                "missing_descriptions": len(audit["missing_descriptions"]),
                "exact_shopify_matches": exact,
                "missing_shopify_matches": missing,
                "ambiguous_shopify_matches": ambiguous,
                "tax_included_confirmed": True,
                "tax_rate": tax_rate,
                "external_writes": 0,
                "audit_payload": {
                    "batch_key": batch_key,
                    "sample_rows": sample_rows,
                    "normalization": "Both period and comma are treated as thousands separators; decimals are not accepted.",
                    "discounts": "No discount was provided or applied.",
                },
            },
        )
        CatalogHistoryEvent.objects.update_or_create(
            entity_type="supplier_catalog_import",
            entity_id=str(import_record.id),
            action="baru_pdf_imported_local",
            defaults={
                "after": {
                    "sha256": audit["source_sha256"], "rows": len(rows), "exact": exact,
                    "missing": missing, "ambiguous": ambiguous, "duplicates": duplicates,
                    "tax_treatment": "INCLUDED", "externalWrites": 0,
                },
                "reversible": True,
                "actor_label": "local-baru-import",
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Barú PDF → SQLite: {len(rows)} filas, {audit['unique_skus']} SKU únicos, "
            f"duplicados {duplicates}, precios inválidos 0; Shopify exactos {exact}, "
            f"catálogo-only {missing}, ambiguos {ambiguous}; IVA incluido {tax_rate}%; externalWrites=0."
        ))
