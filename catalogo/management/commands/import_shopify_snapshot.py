import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import ChannelSnapshot, CostObservation, IntegrationReadStatus, InventoryLevel, InventorySourceSnapshot
from catalogo.models import MasterProduct, ProductImage, ProductMetafield, ProductVariant, ProviderConfig, ShopifyImportState


def nodes(connection):
    if isinstance(connection, list):
        return connection
    if not isinstance(connection, dict):
        return []
    if isinstance(connection.get("nodes"), list):
        return connection["nodes"]
    return [edge.get("node", {}) for edge in connection.get("edges", []) if isinstance(edge, dict)]


def money(value):
    if isinstance(value, dict):
        value = value.get("amount")
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise CommandError("Shopify entregó un importe no numérico; se abortó la importación local.") from error


def quantity(level, name="available"):
    for item in level.get("quantities") or []:
        if item.get("name") == name:
            return money(item.get("quantity"))
    return money(level.get(name))


def observed(value):
    return parse_datetime(value or "") or timezone.now()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def normalized_products(payload):
    root = payload.get("data", payload)
    products = root.get("products")
    if isinstance(products, list):
        return products
    if isinstance(products, dict) and nodes(products):
        return nodes(products)
    grouped = defaultdict(lambda: {"variants": {"nodes": []}})
    for variant in nodes(root.get("productVariants")):
        product = dict(variant.get("product") or {})
        product_id = product.get("id")
        if product_id:
            grouped[product_id].update(product)
            grouped[product_id]["variants"]["nodes"].append({key: value for key, value in variant.items() if key != "product"})
    return list(grouped.values())


class Command(BaseCommand):
    help = "Importa a SQLite un JSON ya leído de Shopify. Nunca llama ni escribe Shopify."

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as error:
            raise CommandError(f"JSON inválido: {error}") from error
        products = normalized_products(payload)
        complete = payload.get("complete") is True
        if complete and not products:
            raise CommandError("Una lectura Shopify completa vacía no puede reemplazar el último snapshot correcto.")
        imported_products = imported_variants = 0
        seen_product_ids = set()
        seen_variant_ids = set()
        field_counts = defaultdict(int)
        for raw in products:
            product_id = str(raw.get("id") or "")
            if not product_id:
                continue
            seen_product_ids.add(product_id)
            collection_rows = nodes(raw.get("collections"))
            collections = [item.get("title") or item.get("handle") or item.get("id") for item in collection_rows if item]
            product_metafields = nodes(raw.get("metafields"))
            media = nodes(raw.get("media"))
            featured = (((raw.get("featuredMedia") or {}).get("preview") or {}).get("image") or {})
            if featured.get("url"):
                media = [{"preview": {"image": featured}}] + media
            product_missing = [name for name, present in (("collections", collections), ("metafields", product_metafields)) if not present]
            product, _ = MasterProduct.objects.update_or_create(
                shopify_product_id=product_id,
                defaults={
                    "title": raw.get("title") or f"Shopify {product_id}", "vendor": raw.get("vendor") or "",
                    "brand": raw.get("vendor") or "", "category": ((raw.get("category") or {}).get("fullName") if isinstance(raw.get("category"), dict) else "") or raw.get("productType") or "",
                    "product_type": raw.get("productType") or "", "description_html": raw.get("descriptionHtml") or raw.get("description") or "",
                    "status": raw.get("status") or "DRAFT", "tags": raw.get("tags") or [], "collections": collections,
                    "quality_score": max(20, 100 - len(product_missing) * 10), "missing_fields": product_missing, "needs_review": bool(product_missing),
                },
            )
            imported_products += 1
            seen_meta = set()
            for meta in product_metafields:
                namespace, key = str(meta.get("namespace") or ""), str(meta.get("key") or "")
                if not namespace or not key:
                    continue
                value = meta.get("jsonValue", meta.get("value", ""))
                ProductMetafield.objects.update_or_create(product=product, namespace=namespace, key=key, defaults={
                    "value": json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value,
                    "value_type": meta.get("type") or "",
                })
                seen_meta.add((namespace, key))
            if seen_meta:
                stale = ProductMetafield.objects.filter(product=product)
                for existing in stale:
                    if (existing.namespace, existing.key) not in seen_meta:
                        existing.delete()
            ProductImage.objects.filter(product=product).delete()
            seen_urls = set()
            for position, item in enumerate(media, start=1):
                image = ((item.get("preview") or {}).get("image") or {})
                url = image.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    ProductImage.objects.create(product=product, position=position, source_url=url, alt_text=image.get("altText") or item.get("alt") or "")

            for raw_variant in nodes(raw.get("variants")):
                variant_id = str(raw_variant.get("id") or "")
                if not variant_id:
                    continue
                seen_variant_ids.add(variant_id)
                compare_at = money(raw_variant.get("compareAtPrice"))
                item = raw_variant.get("inventoryItem") or {}
                unit_cost_payload = item.get("unitCost")
                unit_cost = money(unit_cost_payload)
                levels_connection = item.get("inventoryLevels") or {}
                levels = nodes(levels_connection)
                levels_page_info = levels_connection.get("pageInfo") if isinstance(levels_connection, dict) else None
                levels_complete = bool(levels_page_info) and not levels_page_info.get("hasNextPage", False)
                total = money(raw_variant.get("inventoryQuantity"))
                variant, _ = ProductVariant.objects.update_or_create(shopify_variant_id=variant_id, defaults={
                    "product": product, "sku": raw_variant.get("sku") or "", "title": raw_variant.get("title") or "",
                    "barcode": raw_variant.get("barcode") or "", "price": money(raw_variant.get("price")),
                    "compare_at_price": compare_at, "provider_cost": unit_cost, "inventory_policy": raw_variant.get("inventoryPolicy") or "",
                })
                imported_variants += 1
                field_counts["compare_at_price"] += int(compare_at is not None)
                field_counts["variant_unit_cost"] += int(unit_cost is not None)
                CostObservation.objects.update_or_create(
                    variant=variant, source=CostObservation.Source.SHOPIFY,
                    evidence_reference="Shopify Admin GraphQL inventoryItem.unitCost",
                    defaults={"raw_cost": unit_cost, "derived_net_cost": None,
                              "currency": unit_cost_payload.get("currencyCode", "COP") if isinstance(unit_cost_payload, dict) else "COP",
                              "tax_treatment": ProviderConfig.TaxTreatment.PENDING, "tax_rate": None,
                              "observed_at": timezone.now(),
                              "payload_fingerprint": fingerprint({"variant": variant_id, "unitCost": str(unit_cost)})},
                )
                InventoryLevel.objects.filter(variant=variant).delete()
                InventorySourceSnapshot.objects.filter(variant=variant, source_name__startswith="Shopify").delete()
                availability = []
                for level in levels:
                    location = level.get("location") or {}
                    address = location.get("address") or {}
                    location_id = str(location.get("id") or level.get("id") or "UNKNOWN")
                    available = quantity(level)
                    availability.append(available)
                    level_time = observed(level.get("updatedAt") or raw_variant.get("updatedAt"))
                    InventoryLevel.objects.create(
                        variant=variant,
                        location_external_id=location_id,
                        location_name=location.get("name") or "Ubicación sin nombre",
                        available=available,
                        observed_at=level_time,
                        origin_address={
                            key: address.get(key)
                            for key in (
                                "address1", "address2", "city", "province",
                                "provinceCode", "zip", "country", "countryCode",
                            )
                            if address.get(key) not in (None, "")
                        },
                        address_verified=bool(location.get("addressVerified")),
                        fulfills_online_orders=bool(location.get("fulfillsOnlineOrders")),
                        location_active=location.get("isActive") is not False,
                    )
                    InventorySourceSnapshot.objects.create(
                        variant=variant, source_name="Shopify ubicación", warehouse_external_id=location_id,
                        warehouse_name=location.get("name") or "Ubicación sin nombre", reported_stock=available,
                        reserved_stock=quantity(level, "reserved") or 0, safety_stock=0, available_to_promise=available,
                        stock_unknown=available is None, observed_at=level_time, freshness_minutes=60,
                        update_method=InventorySourceSnapshot.UpdateMethod.API, canonical=False,
                        evidence_reference="Shopify Admin GraphQL inventoryLevels.quantities",
                    )
                if total is None and levels_complete and levels and all(value is not None for value in availability):
                    total = sum(availability, Decimal("0"))
                if levels:
                    field_counts["inventory_by_location"] += len(levels)
                InventorySourceSnapshot.objects.create(
                    variant=variant, source_name="Shopify disponibilidad canónica", warehouse_external_id="ALL_LOCATIONS",
                    warehouse_name="Total Shopify; desglose completo" if levels_complete else "Total Shopify; desglose de ubicaciones parcial",
                    reported_stock=total, reserved_stock=0, safety_stock=0, available_to_promise=total,
                    stock_unknown=total is None, observed_at=observed(raw_variant.get("updatedAt")), freshness_minutes=60,
                    update_method=InventorySourceSnapshot.UpdateMethod.API,
                    canonical=True, evidence_reference="Shopify inventoryQuantity; inventoryLevels complete" if levels_complete else "Shopify inventoryQuantity; inventoryLevels partial",
                )
                variant_meta = nodes(raw_variant.get("metafields"))
                missing = [name for name, value in (("compare_at_price", compare_at), ("cost", unit_cost)) if value is None]
                missing += ([] if levels_complete else ["inventory_by_location_complete"]) + ([] if variant_meta else ["variant_metafields"])
                ChannelSnapshot.objects.update_or_create(product=product, variant=variant, channel="SHOPIFY", defaults={
                    "external_product_id": product_id, "external_variant_id": variant_id, "state": raw.get("status") or "UNKNOWN",
                    "price": variant.price, "compare_at_price": compare_at, "cost": unit_cost, "inventory_available": total,
                    "quality_score": max(20, 100 - len(product_missing + missing) * 10),
                    "payload": {"source": "secure-shopify-read", "partial": bool(product_missing or missing), "handle": raw.get("handle") or "",
                                "missing": product_missing + missing, "selectedOptions": raw_variant.get("selectedOptions") or [],
                                "variantMetafields": variant_meta, "requiresShipping": item.get("requiresShipping"),
                                "tracked": item.get("tracked"), "inventoryItemId": item.get("id"),
                                "weight": ((item.get("measurement") or {}).get("weight")),
                                "inventoryLocationsPartial": not levels_complete},
                    "observed_at": observed(raw_variant.get("updatedAt") or raw.get("updatedAt")),
                })

        if complete:
            MasterProduct.objects.exclude(shopify_product_id__in=seen_product_ids).exclude(
                shopify_product_id__isnull=True,
            ).exclude(shopify_product_id="").update(status="STALE_LOCAL_SNAPSHOT", needs_review=True)
            ChannelSnapshot.objects.filter(channel="SHOPIFY").exclude(
                external_variant_id__in=seen_variant_ids,
            ).update(state="STALE", inventory_available=None, quality_score=0)

        root = payload.get("data", payload)
        connection = root.get("productVariants") or root.get("products") or {}
        page_info = connection.get("pageInfo", {}) if isinstance(connection, dict) else {}
        ShopifyImportState.objects.update_or_create(key="PRIMARY", defaults={
            "status": "SUCCEEDED", "next_cursor": payload.get("end_cursor") or page_info.get("endCursor") or "",
            "last_success_at": timezone.now(), "pages_processed": payload.get("pages", 1),
            "products_processed": imported_products, "variants_processed": imported_variants, "last_error": "",
        })
        incremental = payload.get("incremental") is True
        current_variant_count = ProductVariant.objects.exclude(
            product__status="STALE_LOCAL_SNAPSHOT",
        ).exclude(shopify_variant_id="").count()
        IntegrationReadStatus.objects.update_or_create(
            system="SHOPIFY", capability="marketplace_catalog_snapshot", defaults={
                "status": IntegrationReadStatus.Status.AVAILABLE if complete or incremental else IntegrationReadStatus.Status.PARTIAL,
                "message": (
                    "Catálogo Shopify completo persistido en SQLite local." if complete
                    else "Cambios recientes de Shopify aplicados al snapshot local." if incremental
                    else "Lectura Shopify parcial persistida localmente."
                ),
                "evidence_reference": "Shopify Admin GraphQL read-only",
                "record_count": current_variant_count if incremental else imported_variants,
                "observed_at": timezone.now(), "last_success_at": timezone.now(), "external_writes": 0,
                "details": {
                    "products": imported_products, "variants": imported_variants,
                    "catalog_variants": current_variant_count, "complete": complete,
                    "incremental": incremental, "externalWrites": 0,
                },
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Snapshot local: {imported_products} productos, {imported_variants} variantes; compare-at {field_counts['compare_at_price']}, costos {field_counts['variant_unit_cost']}, niveles {field_counts['inventory_by_location']}. externalWrites=0."
        ))
