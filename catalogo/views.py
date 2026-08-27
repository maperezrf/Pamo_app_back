from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256

from django.core.cache import cache
from django.db import connection
from django.db.models import Avg, Case, Count, IntegerField, Max, OuterRef, Prefetch, Q, Subquery, Sum, When
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from config.constants import EXTERNAL_WRITES_ENABLED, SHOPIFY_READS_ENABLED

from .models import (
    CatalogHistoryEvent,
    Channel,
    ChannelSnapshot,
    ExternalChannelProductSnapshot,
    MasterProduct,
    PriceCalculation,
    PricingPolicy,
    ProductVariant,
    ProviderConfig,
    SodimacCatalogObservation,
    SodimacCatalogLink,
    ShopifyImportState,
    SiigoProductSnapshot,
    SkuReconciliation,
    SupplierCatalogImport,
    IntegrationReadStatus,
    PhysicalEnrichmentPilotSelection,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    PhysicalMeasurementImportBatch,
    LogisticsQuoteSnapshot,
    PricingLocalBatch,
    MultwarehouseSimulationRun,
    ShopifyPhysicalUpdatePreview,
)
from .permissions import LocalOrAuthenticatedCatalogAccess
from .pricing import PricingInputError, calculate_price, shipping_options
from .serializers import (
    HistorySerializer,
    ImportStateSerializer,
    MasterProductSerializer,
    PricingPolicySerializer,
    ProviderConfigSerializer,
    ReconciliationSerializer,
    SupplierCatalogImportSerializer,
    IntegrationReadStatusSerializer,
    ExternalChannelProductSnapshotSerializer,
    ChannelSnapshotSerializer,
    VariantSerializer,
)
from .pilot import build_bulk_metrics
from .physical import build_shopify_preview
from .envia_quote import EnviaQuoteContractError, run_fixture_quote
from .envia_readiness import readiness_sets, serialize_variant_shipping_intelligence
from .phase7 import build_average_shipping_reference
from .physical_measurements import (
    PhysicalMeasurementImportError,
    apply_measurement_import,
    create_measurement_task,
    measurement_workspace,
    preview_measurement_import,
    reverse_measurement_import,
    serialize_import_batch,
)
from .phase6_services import (
    Phase6InputError,
    apply_pricing_batch,
    create_pricing_preview,
    reverse_pricing_batch,
    run_multwarehouse,
    save_rule,
    serialize_batch,
    serialize_rule,
)
from .phase7 import build_phase7_workspace
from .sodimac_catalog import (
    SodimacCatalogError,
    apply_sodimac_import,
    build_sodimac_workspace,
    enqueue_incremental_audits,
    preview_sodimac_import,
    reverse_sodimac_import,
    serialize_batch as serialize_sodimac_batch,
)
from .sodimac_kits import reverse_sodimac_kit_import
from .channel_refresh import get_channel_refresh_state, start_channel_refresh
from .commercial_costs import enrich_commercial_payload
import json
from pathlib import Path


class CatalogChannelRefreshAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        return Response(get_channel_refresh_state())

    def post(self, request):
        if connection.vendor != "sqlite":
            return Response({
                "detail": "Esta sincronización está limitada al laboratorio SQLite local.",
                "external_writes": 0,
            }, status=409)
        state, started = start_channel_refresh()
        return Response(state, status=202 if started else 200)


CHANNEL_TABLE_CODES = {
    "SHOPIFY", "MERCADO_LIBRE", "FALABELLA", "SODIMAC", "MADECENTRO", "RAPPI",
}
CHANNEL_TABLE_METRICS = {
    "status", "price", "commission", "costs", "profit", "target",
    "shipping", "quality", "missing",
}
CHANNEL_TABLE_COLUMN_KEYS = {
    f"{channel}__{metric}"
    for channel in CHANNEL_TABLE_CODES
    for metric in CHANNEL_TABLE_METRICS
}
CATALOG_COLUMN_KEYS = {
    "photo", "sku", "product", "provider", "cost", "siigo", "price", "margin",
    "SHOPIFY", "MERCADO_LIBRE", "FALABELLA", "MADECENTRO", "RAPPI",
    "sodimac", "envia", "shipping", "quality", "missing",
} | CHANNEL_TABLE_COLUMN_KEYS


def shopify_master_variants():
    return ProductVariant.objects.exclude(
        product__status="STALE_LOCAL_SNAPSHOT",
    ).exclude(
        product__shopify_product_id__isnull=True,
    ).exclude(
        product__shopify_product_id="",
    ).exclude(
        shopify_variant_id__isnull=True,
    ).exclude(
        shopify_variant_id="",
    )


def _cop_label(value):
    if value is None:
        return "—"
    rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"$ {int(rounded):,}".replace(",", ".")


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _channel_profit_estimate(channel, channel_price, cost, snapshot, seller_shipping):
    """Return verified profit when present, otherwise a clearly provisional base."""
    price = _decimal_or_none(channel_price)
    product_cost = _decimal_or_none(cost)
    if price is None or product_cost is None:
        return None, False, None, None
    commercial_payload = enrich_commercial_payload(
        channel,
        channel_price,
        snapshot.payload if snapshot else {},
    )
    commercial = commercial_payload.get("profitability") or {}
    verified_profit = _decimal_or_none(commercial.get("net_profit"))
    if commercial.get("verified") and verified_profit is not None:
        return verified_profit, True, _decimal_or_none(commercial.get("commission_amount")), _decimal_or_none(commercial.get("other_cost_amount"))
    commission = _decimal_or_none(commercial.get("commission_amount"))
    other_costs = _decimal_or_none(commercial.get("other_cost_amount"))
    shipping = _decimal_or_none(seller_shipping)
    estimate = price - product_cost - (commission or Decimal("0")) - (other_costs or Decimal("0")) - (shipping or Decimal("0"))
    return estimate, False, commission, other_costs


def catalog_column_index():
    """Small cached projection used for global Excel-like filtering/sorting."""
    signature = shopify_master_variants().aggregate(
        total=Count("id"),
        latest=Max("product__updated_at"),
    )
    latest = signature["latest"].isoformat() if signature["latest"] else "empty"
    cache_key = f"catalog-column-index:v6:{signature['total']}:{latest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    average_shipping_reference = build_average_shipping_reference()
    variants = list(
        shopify_master_variants().select_related(
            "product", "canonical_cost__observation",
        ).prefetch_related(
            "product__images", "channel_snapshots", "sodimac_catalog_links",
            "sodimac_catalog_links__observations", "cost_observations",
            "siigo_snapshots",
            Prefetch("physical_candidates", queryset=PhysicalEvidenceCandidate.objects.filter(
                scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
                classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
                conflict=False,
            ).prefetch_related("decisions"), to_attr="envia_physical_candidates"),
        )
    )
    external_ids = {
        str((snapshot.payload or {}).get("external_snapshot_id"))
        for variant in variants
        for snapshot in variant.channel_snapshots.all()
        if (snapshot.payload or {}).get("external_snapshot_id")
    }
    external_snapshots = {
        str(snapshot.id): snapshot
        for snapshot in ExternalChannelProductSnapshot.objects.filter(id__in=external_ids)
    }
    package_fields, package_complete, quoted = readiness_sets()
    quote_map = {}
    for quote in LogisticsQuoteSnapshot.objects.filter(
        variant__isnull=False,
        provider="ENVIA",
        basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
        status="AVAILABLE",
        amount__isnull=False,
    ).order_by("variant_id", "-observed_at", "amount"):
        quote_map.setdefault(str(quote.variant_id), quote)

    meli_seller = {}
    for variant_id, payload in ExternalChannelProductSnapshot.objects.filter(
        channel=Channel.MERCADO_LIBRE,
        active=True,
        matched_variant__isnull=False,
    ).values_list("matched_variant_id", "payload"):
        estimate = ((payload or {}).get("shipping_costs") or {}).get("seller_estimate")
        if estimate is not None:
            meli_seller.setdefault(str(variant_id), estimate)

    required_package_fields = {"WEIGHT", "LENGTH", "WIDTH", "HEIGHT"}
    index = {}
    for variant in variants:
        variant_id = str(variant.id)
        product = variant.product
        shopify_costs = [
            observation for observation in variant.cost_observations.all()
            if observation.source == "SHOPIFY" and observation.raw_cost is not None
        ]
        latest_shopify_cost = max(
            shopify_costs,
            key=lambda observation: observation.observed_at,
            default=None,
        )
        cost = latest_shopify_cost.raw_cost if latest_shopify_cost else None
        margin = None
        if variant.price and cost is not None and Decimal(variant.price) != 0:
            margin = ((Decimal(variant.price) - Decimal(cost)) / Decimal(variant.price)) * 100
        channel_states = {
            snapshot.channel: snapshot.state or "NO EXISTE"
            for snapshot in variant.channel_snapshots.all()
            if snapshot.state != "STALE"
        }
        active_sodimac = next(
            (link for link in variant.sodimac_catalog_links.all() if link.active),
            None,
        )
        siigo_snapshot = next(
            (
                snapshot for snapshot in variant.siigo_snapshots.all()
                if snapshot.match_status == SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY
            ),
            None,
        )
        if variant.id not in package_complete:
            envia_value = "FALTAN DATOS"
        elif variant.id in quoted:
            envia_value = "COTIZACIÓN DISPONIBLE"
        else:
            envia_value = "LISTO PARA COTIZAR"
        quote = quote_map.get(variant_id)
        missing_package = len(required_package_fields - package_fields.get(variant.id, set()))
        missing_total = len(product.missing_fields or []) + missing_package
        snapshots_by_channel = {
            snapshot.channel: snapshot
            for snapshot in variant.channel_snapshots.all()
            if snapshot.state != "STALE"
        }
        sodimac_observation = active_sodimac.observations.first() if active_sodimac else None
        shipping_intelligence = serialize_variant_shipping_intelligence(
            variant,
            external_snapshots,
            average_shipping_reference,
        )
        average_shipping = shipping_intelligence.get("average_shipping") or {}
        shipping_amount = average_shipping.get("amount")
        shipping_band = average_shipping.get("tariff_band")
        shipping_value = (
            f"{_cop_label(shipping_amount)} · {shipping_band} · estimado"
            if shipping_amount is not None else "PENDIENTE"
        )
        shipping_sort = float(shipping_amount) if shipping_amount is not None else None
        channel_values = {}
        channel_sorts = {}
        for channel_code in CHANNEL_TABLE_CODES:
            snapshot = snapshots_by_channel.get(channel_code)
            sodimac_exists = bool(
                channel_code == "SODIMAC"
                and active_sodimac
                and active_sodimac.status == SodimacCatalogLink.Status.LINKED_EXACT
            )
            exists = sodimac_exists if channel_code == "SODIMAC" else snapshot is not None
            if channel_code == "SODIMAC":
                state = (
                    sodimac_observation.publication_state
                    if sodimac_exists and sodimac_observation and sodimac_observation.publication_state
                    else "LINKED_EXACT" if sodimac_exists else "NO CREADO"
                )
                channel_price = None
                inventory = sodimac_observation.inventory_available if sodimac_observation else None
                quality_score = sodimac_observation.overall_score if sodimac_observation else None
            else:
                state = (snapshot.state or "NO CREADO") if snapshot else "NO CREADO"
                channel_price = (
                    snapshot.price if snapshot and snapshot.price is not None
                    else variant.price if channel_code == "SHOPIFY" else None
                )
                inventory = snapshot.inventory_available if snapshot else None
                quality_score = None
                if snapshot and channel_code == "SHOPIFY":
                    quality_score = snapshot.quality_score
                elif snapshot and channel_code == "FALABELLA":
                    external_id = str((snapshot.payload or {}).get("external_snapshot_id") or "")
                    external = external_snapshots.get(external_id)
                    raw_score = ((external.payload or {}).get("content_score") if external else None)
                    try:
                        quality_score = int(float(raw_score)) if raw_score is not None else None
                    except (TypeError, ValueError):
                        quality_score = None

            shipping_record = (shipping_intelligence.get("channels") or {}).get(channel_code)
            if (
                channel_code == "SHOPIFY"
                and not (
                    shipping_record
                    and (
                        shipping_record.get("seller_estimate") is not None
                        or shipping_record.get("buyer_charge") is not None
                    )
                )
                and (shipping_intelligence.get("carrier_quote") or {}).get("amount") is not None
            ):
                shipping_record = {
                    "seller_estimate": shipping_intelligence["carrier_quote"]["amount"],
                    "buyer_charge": None,
                }
            seller_shipping = shipping_record.get("seller_estimate") if shipping_record else None
            buyer_shipping = shipping_record.get("buyer_charge") if shipping_record else None
            shipping_available = seller_shipping is not None or buyer_shipping is not None
            average_display_amount = (
                average_shipping.get("amount")
                if channel_code == "SHOPIFY" and not shipping_available
                else None
            )
            if average_display_amount is not None:
                shipping_label = (
                    f"Promedio estimado {_cop_label(average_display_amount)} · "
                    "Cliente — · informativo"
                )
            elif shipping_record:
                shipping_label = (
                    f"Empresa {_cop_label(seller_shipping)} · "
                    f"Cliente {_cop_label(buyer_shipping)}"
                )
            else:
                shipping_label = "Pendiente"

            profit, _profit_verified, commission_amount, other_cost_amount = (
                _channel_profit_estimate(
                    channel_code,
                    channel_price,
                    cost,
                    snapshot,
                    seller_shipping,
                )
            )
            commercial_payload = enrich_commercial_payload(
                channel_code,
                channel_price,
                snapshot.payload if snapshot else {},
            )
            commercial = commercial_payload.get("profitability") or {}
            target_value = _decimal_or_none(commercial.get("target_value"))
            target_label = commercial.get("target_label") or (
                _cop_label(target_value) if target_value is not None else None
            )

            missing_items = []
            if not exists:
                missing_items = ["Publicación"]
            else:
                if channel_price is None:
                    missing_items.append("Precio")
                if cost is None:
                    missing_items.append("Costo")
                if commission_amount is None or other_cost_amount is None:
                    missing_items.append("Comisión/cargos")
                if profit is None:
                    missing_items.append("Utilidad")
                if not shipping_available:
                    missing_items.append("Envío")
                if quality_score is None:
                    missing_items.append("Calidad")
                if inventory is None:
                    missing_items.append("Inventario")
                if not shipping_available and missing_package:
                    missing_items.append("Peso/medidas")
            missing_count = len(dict.fromkeys(missing_items))

            prefix = f"{channel_code}__"
            channel_values.update({
                f"{prefix}status": state,
                f"{prefix}price": _cop_label(channel_price),
                f"{prefix}commission": _cop_label(commission_amount) if commission_amount is not None else "Pendiente" if exists else "—",
                f"{prefix}costs": _cop_label(other_cost_amount) if other_cost_amount is not None else "Pendiente" if exists else "—",
                f"{prefix}profit": _cop_label(profit) if profit is not None else "Pendiente" if exists else "—",
                f"{prefix}target": target_label or ("Pendiente" if exists else "—"),
                f"{prefix}shipping": shipping_label,
                f"{prefix}quality": str(quality_score) if quality_score is not None else "—",
                f"{prefix}missing": f"{missing_count} faltantes" if missing_count else "Completo",
            })
            channel_sorts.update({
                f"{prefix}status": state,
                f"{prefix}price": float(channel_price) if channel_price is not None else None,
                f"{prefix}commission": float(commission_amount) if commission_amount is not None else None,
                f"{prefix}costs": float(other_cost_amount) if other_cost_amount is not None else None,
                f"{prefix}profit": float(profit) if profit is not None else None,
                f"{prefix}target": float(target_value) if target_value is not None else None,
                f"{prefix}shipping": (
                    float(seller_shipping if seller_shipping is not None else buyer_shipping)
                    if shipping_available
                    else float(average_display_amount) if average_display_amount is not None else None
                ),
                f"{prefix}quality": float(quality_score) if quality_score is not None else None,
                f"{prefix}missing": missing_count,
            })
        values = {
            "photo": "Con imagen" if any(image.source_url for image in product.images.all()) else "Sin imagen",
            "sku": variant.sku or "SKU pendiente",
            "product": product.title or "Nombre pendiente",
            "provider": product.vendor or "Pendiente",
            "cost": _cop_label(cost),
            "siigo": "CREADO" if siigo_snapshot else "FALTA CREAR",
            "price": _cop_label(variant.price),
            "margin": f"{float(margin):.1f}%" if margin is not None else "Pendiente",
            "SHOPIFY": channel_states.get(Channel.SHOPIFY, "NO EXISTE"),
            "MERCADO_LIBRE": channel_states.get(Channel.MERCADO_LIBRE, "NO EXISTE"),
            "FALABELLA": channel_states.get(Channel.FALABELLA, "NO EXISTE"),
            "MADECENTRO": channel_states.get(Channel.MADECENTRO, "NO EXISTE"),
            "RAPPI": channel_states.get(Channel.RAPPI, "NO EXISTE"),
            "sodimac": active_sodimac.status if active_sodimac else "UNLINKED",
            "envia": envia_value,
            "shipping": shipping_value,
            "quality": str(product.quality_score),
            "missing": str(missing_total),
            **channel_values,
        }
        sorts = {
            **values,
            "photo": 1 if values["photo"] == "Con imagen" else 0,
            "cost": float(cost) if cost is not None else None,
            "siigo": 1 if siigo_snapshot else 0,
            "price": float(variant.price) if variant.price is not None else None,
            "margin": float(margin) if margin is not None else None,
            "quality": product.quality_score,
            "missing": missing_total,
            "shipping": shipping_sort,
            **channel_sorts,
        }
        index[variant_id] = {"values": values, "sorts": sorts}
    cache.set(cache_key, index, timeout=60)
    return index


def parse_catalog_column_filters(request):
    raw = request.query_params.get("column_filters", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        key: [str(value) for value in values[:5000]]
        for key, values in parsed.items()
        if key in CATALOG_COLUMN_KEYS and isinstance(values, list) and values
    }


def apply_catalog_column_filters(queryset, column_filters):
    if not column_filters:
        return queryset

    # Base catalog values already live in indexed relational fields. Avoid the
    # full derived catalog projection for these common filters: that projection
    # also calculates costs, shipping and channel metrics for every variant.
    remaining_filters = dict(column_filters)
    shopify_states = remaining_filters.pop("SHOPIFY__status", None)
    if shopify_states:
        queryset = queryset.filter(product__status__in=shopify_states)

    providers = remaining_filters.pop("provider", None)
    if providers:
        queryset = queryset.filter(product__vendor__in=providers)

    product_names = remaining_filters.pop("product", None)
    if product_names:
        queryset = queryset.filter(product__title__in=product_names)

    sku_values = remaining_filters.pop("sku", None)
    if sku_values:
        real_skus = [value for value in sku_values if value != "SKU pendiente"]
        sku_query = Q(sku__in=real_skus)
        if "SKU pendiente" in sku_values:
            sku_query |= Q(sku="") | Q(sku__isnull=True)
        queryset = queryset.filter(sku_query)

    photo_values = set(remaining_filters.pop("photo", None) or [])
    if photo_values == {"Con imagen"}:
        queryset = queryset.filter(product__images__source_url__gt="")
    elif photo_values == {"Sin imagen"}:
        queryset = queryset.exclude(product__images__source_url__gt="")

    siigo_values = set(remaining_filters.pop("siigo", None) or [])
    if siigo_values and siigo_values != {"CREADO", "FALTA CREAR"}:
        siigo_exact = Q(
            siigo_snapshots__match_status=SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY,
            siigo_snapshots__matched_variant__isnull=False,
        )
        if siigo_values == {"CREADO"}:
            queryset = queryset.filter(siigo_exact)
        elif siigo_values == {"FALTA CREAR"}:
            queryset = queryset.exclude(siigo_exact)

    if not remaining_filters:
        return queryset

    index = catalog_column_index()
    candidate_ids = {str(value) for value in queryset.values_list("id", flat=True)}
    for key, selected_values in remaining_filters.items():
        selected = set(selected_values)
        candidate_ids = {
            variant_id for variant_id in candidate_ids
            if index.get(variant_id, {}).get("values", {}).get(key) in selected
        }
    return queryset.filter(id__in=candidate_ids)


class CatalogWorkspaceAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        actor = request.user.pk if getattr(request.user, "is_authenticated", False) else "local"
        cache_fingerprint = sha256(request.query_params.urlencode().encode("utf-8")).hexdigest()[:20]
        cache_key = f"catalog-workspace:v2:{actor}:{cache_fingerprint}"
        if request.query_params.get("fresh") != "1":
            cached_payload = cache.get(cache_key)
            if cached_payload is not None:
                response = Response(cached_payload)
                response["X-Pamo-Cache"] = "HIT"
                response["Cache-Control"] = "private, max-age=30, stale-while-revalidate=300"
                return response

        page = max(int(request.query_params.get("page", 1)), 1)
        page_size = min(max(int(request.query_params.get("page_size", 50)), 25), 100)
        base_variants = shopify_master_variants()
        all_products = MasterProduct.objects.filter(
            id__in=base_variants.values("product_id"),
        ).distinct()
        filtered_variants = base_variants
        search = request.query_params.get("search", "").strip()
        if search:
            filtered_variants = filtered_variants.filter(
                Q(product__title__icontains=search) | Q(product__vendor__icontains=search)
                | Q(product__brand__icontains=search) | Q(product__category__icontains=search)
                | Q(sku__icontains=search) | Q(barcode__icontains=search)
            )
        if request.query_params.get("provider"):
            filtered_variants = filtered_variants.filter(product__vendor=request.query_params["provider"])
        if request.query_params.get("status"):
            filtered_variants = filtered_variants.filter(product__status=request.query_params["status"])
        siigo_status = request.query_params.get("siigoStatus", "").strip().lower()
        siigo_exact = Q(
            siigo_snapshots__match_status=SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY,
            siigo_snapshots__matched_variant__isnull=False,
        )
        if siigo_status == "created":
            filtered_variants = filtered_variants.filter(siigo_exact)
        elif siigo_status == "missing":
            filtered_variants = filtered_variants.exclude(siigo_exact)
        if request.query_params.get("brand"):
            filtered_variants = filtered_variants.filter(product__brand=request.query_params["brand"])
        if request.query_params.get("category"):
            filtered_variants = filtered_variants.filter(product__category=request.query_params["category"])
        collection = request.query_params.get("collection", "").strip()
        if collection:
            collection_product_ids = [
                product_id
                for product_id, collections in all_products.values_list("id", "collections")
                if collection in (collections or [])
            ]
            filtered_variants = filtered_variants.filter(product_id__in=collection_product_ids)
        channel = request.query_params.get("channel", "").strip().upper()
        channel_coverage = request.query_params.get("channelCoverage", "").strip().lower()
        channel_state = request.query_params.get("channelState", "").strip()
        if channel == Channel.SHOPIFY:
            if channel_coverage == "missing":
                filtered_variants = filtered_variants.none()
            if channel_state:
                filtered_variants = filtered_variants.filter(product__status=channel_state)
        elif channel == "SIIGO":
            if channel_coverage == "missing":
                filtered_variants = filtered_variants.exclude(siigo_exact)
            elif channel_coverage == "created" or channel_state:
                filtered_variants = filtered_variants.filter(siigo_exact)
            if channel_state == "ACTIVE":
                filtered_variants = filtered_variants.filter(siigo_snapshots__active=True)
            elif channel_state == "INACTIVE":
                filtered_variants = filtered_variants.filter(siigo_snapshots__active=False)
        elif channel == Channel.SODIMAC:
            linked_sodimac = Q(
                sodimac_catalog_links__active=True,
                sodimac_catalog_links__status=SodimacCatalogLink.Status.LINKED_EXACT,
            )
            if channel_coverage == "missing":
                filtered_variants = filtered_variants.exclude(linked_sodimac)
            elif channel_coverage == "created" or channel_state:
                filtered_variants = filtered_variants.filter(linked_sodimac)
            if channel_state:
                filtered_variants = filtered_variants.filter(
                    sodimac_catalog_links__status=channel_state,
                )
        elif channel:
            channel_rows = {"channel_snapshots__channel": channel}
            if channel_state:
                channel_rows["channel_snapshots__state"] = channel_state
            if channel_coverage == "missing":
                filtered_variants = filtered_variants.exclude(
                    channel_snapshots__channel=channel,
                )
            elif channel_coverage == "created" or channel_state:
                filtered_variants = filtered_variants.filter(**channel_rows)
        if request.query_params.get("quality") == "low":
            filtered_variants = filtered_variants.filter(product__quality_score__lt=60)
        if request.query_params.get("quality") == "ready":
            filtered_variants = filtered_variants.filter(product__quality_score__gte=80)
        if request.query_params.get("missing") == "yes":
            filtered_variants = filtered_variants.exclude(product__missing_fields=[])
        if request.query_params.get("missing") == "no":
            filtered_variants = filtered_variants.filter(product__missing_fields=[])
        if request.query_params.get("review") in {"yes", "no"}:
            filtered_variants = filtered_variants.filter(product__needs_review=request.query_params["review"] == "yes")
        price_filters = {}
        if request.query_params.get("priceMin"):
            price_filters["gte"] = request.query_params["priceMin"]
        if request.query_params.get("priceMax"):
            price_filters["lte"] = request.query_params["priceMax"]
        if price_filters:
            if channel in {Channel.MERCADO_LIBRE, Channel.FALABELLA, Channel.MADECENTRO, Channel.RAPPI}:
                filtered_variants = filtered_variants.filter(
                    channel_snapshots__channel=channel,
                    **{
                        f"channel_snapshots__price__{lookup}": value
                        for lookup, value in price_filters.items()
                    },
                )
            elif channel == "SIIGO":
                filtered_variants = filtered_variants.filter(
                    **{
                        f"siigo_snapshots__sale_price__{lookup}": value
                        for lookup, value in price_filters.items()
                    },
                )
            elif channel == Channel.SODIMAC:
                filtered_variants = filtered_variants.none()
            else:
                filtered_variants = filtered_variants.filter(
                    **{
                        f"price__{lookup}": value
                        for lookup, value in price_filters.items()
                    },
                )
        if request.query_params.get("costStatus") == "ready":
            filtered_variants = filtered_variants.filter(
                cost_observations__source="SHOPIFY",
                cost_observations__raw_cost__isnull=False,
            ).distinct()
        if request.query_params.get("costStatus") == "pending":
            filtered_variants = filtered_variants.exclude(
                cost_observations__source="SHOPIFY",
                cost_observations__raw_cost__isnull=False,
            )
        _, envia_complete_variant_ids, envia_quoted_variant_ids = readiness_sets()
        envia_readiness = request.query_params.get("enviaReadiness")
        if envia_readiness == "missing":
            filtered_variants = filtered_variants.exclude(id__in=envia_complete_variant_ids)
        elif envia_readiness == "ready":
            filtered_variants = filtered_variants.filter(id__in=envia_complete_variant_ids - envia_quoted_variant_ids)
        elif envia_readiness == "quoted":
            filtered_variants = filtered_variants.filter(id__in=envia_quoted_variant_ids)
        sodimac_link = request.query_params.get("sodimacLink", "").strip()
        if sodimac_link == SodimacCatalogLink.Status.UNLINKED:
            filtered_variants = filtered_variants.exclude(
                sodimac_catalog_links__active=True,
                sodimac_catalog_links__status=SodimacCatalogLink.Status.LINKED_EXACT,
            )
        elif sodimac_link:
            filtered_variants = filtered_variants.filter(
                sodimac_catalog_links__active=True,
                sodimac_catalog_links__status=sodimac_link,
            )
        sodimac_quality = request.query_params.get("sodimacQuality", "").strip()
        sodimac_freshness = request.query_params.get("sodimacFreshness", "").strip()
        sodimac_inventory = request.query_params.get("sodimacInventory", "").strip()
        if sodimac_quality or sodimac_freshness or sodimac_inventory:
            latest_sodimac_observation = SodimacCatalogObservation.objects.filter(
                link__variant=OuterRef("pk"),
                link__active=True,
            ).order_by("-observed_at", "-created_at")
            filtered_variants = filtered_variants.annotate(
                sodimac_latest_severity=Subquery(latest_sodimac_observation.values("severity")[:1]),
                sodimac_latest_expires_at=Subquery(latest_sodimac_observation.values("expires_at")[:1]),
                sodimac_latest_inventory=Subquery(latest_sodimac_observation.values("inventory_available")[:1]),
                sodimac_latest_observed_at=Subquery(latest_sodimac_observation.values("observed_at")[:1]),
            )
        if sodimac_quality:
            filtered_variants = filtered_variants.filter(sodimac_latest_severity=sodimac_quality)
        if sodimac_inventory == "known":
            filtered_variants = filtered_variants.filter(sodimac_latest_inventory__isnull=False)
        elif sodimac_inventory == "unknown":
            filtered_variants = filtered_variants.filter(sodimac_latest_inventory__isnull=True)
        if sodimac_freshness == "current":
            filtered_variants = filtered_variants.filter(sodimac_latest_expires_at__gt=timezone.now())
        elif sodimac_freshness == "stale":
            filtered_variants = filtered_variants.filter(sodimac_latest_expires_at__lte=timezone.now())
        elif sodimac_freshness == "never":
            filtered_variants = filtered_variants.filter(sodimac_latest_observed_at__isnull=True)
        column_filters = parse_catalog_column_filters(request)
        filtered_variants = apply_catalog_column_filters(filtered_variants, column_filters)
        filtered_variants = filtered_variants.distinct()
        total_variants = filtered_variants.count()
        offset = (page - 1) * page_size
        sort_key = request.query_params.get("sort_key", "")
        sort_direction = request.query_params.get("sort_direction", "")
        page_queryset = filtered_variants
        orm_sort_fields = {
            "sku": "sku",
            "product": "product__title",
            "provider": "product__vendor",
            "price": "price",
            "quality": "product__quality_score",
        }
        if sort_key in orm_sort_fields and sort_direction in {"asc", "desc"}:
            prefix = "-" if sort_direction == "desc" else ""
            page_queryset = filtered_variants.order_by(
                f"{prefix}{orm_sort_fields[sort_key]}", "id",
            )
        elif sort_key in CATALOG_COLUMN_KEYS and sort_direction in {"asc", "desc"}:
            index = catalog_column_index()
            candidate_ids = [str(value) for value in filtered_variants.values_list("id", flat=True)]
            reverse = sort_direction == "desc"
            valued_ids = [
                variant_id for variant_id in candidate_ids
                if index.get(variant_id, {}).get("sorts", {}).get(sort_key) is not None
            ]
            missing_ids = sorted(set(candidate_ids) - set(valued_ids))
            valued_ids.sort(
                key=lambda variant_id: (
                    index[variant_id]["sorts"][sort_key],
                    variant_id,
                ),
                reverse=reverse,
            )
            candidate_ids = valued_ids + missing_ids
            page_ids = candidate_ids[offset:offset + page_size]
            preserved_order = Case(
                *[When(id=variant_id, then=position) for position, variant_id in enumerate(page_ids)],
                output_field=IntegerField(),
            )
            page_queryset = filtered_variants.filter(id__in=page_ids).order_by(preserved_order)
            offset = 0
        variants = list(page_queryset.select_related("product").prefetch_related(
            "inventory_levels", "inventory_sources", "cost_observations",
            "canonical_cost__observation", "siigo_snapshots", "supplier_matches__supplier_item__provider",
            "sodimac_catalog_links__observations", "channel_snapshots", "product__images",
            Prefetch("physical_candidates", queryset=PhysicalEvidenceCandidate.objects.filter(
                scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
                classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
                conflict=False,
            ).prefetch_related("decisions"), to_attr="envia_physical_candidates"),
            Prefetch("logistics_quotes", queryset=LogisticsQuoteSnapshot.objects.filter(
                provider="ENVIA", basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
                status="AVAILABLE", amount__isnull=False,
            ).order_by("-observed_at", "amount"), to_attr="envia_current_quotes"),
        )[offset:offset + page_size])
        providers = ProviderConfig.objects.prefetch_related("sku_adjustments")
        policies = PricingPolicy.objects.select_related("provider")
        import_state, _ = ShopifyImportState.objects.get_or_create(key="PRIMARY")
        reconciliations = SkuReconciliation.objects.select_related("supplier_item")[:100]
        missing_shopify = SkuReconciliation.objects.filter(
            status=SkuReconciliation.Status.MISSING,
        ).select_related("supplier_item", "supplier_item__provider")[:1000]
        history = CatalogHistoryEvent.objects.all()[:30]
        meli_shipping_rows = list(ExternalChannelProductSnapshot.objects.filter(
            channel=Channel.MERCADO_LIBRE, active=True, matched_variant__isnull=False,
        ).values_list("matched_variant_id", "payload"))
        meli_seller_variants = {
            variant_id for variant_id, raw in meli_shipping_rows
            if ((raw or {}).get("shipping_costs") or {}).get("seller_estimate") is not None
        }
        meli_buyer_variants = {
            variant_id for variant_id, raw in meli_shipping_rows
            if ((raw or {}).get("shipping_costs") or {}).get("buyer_charge") is not None
        }
        base_variant_count = base_variants.count()
        base_variant_ids = base_variants.values("id")

        def channel_metrics(code, classification="MARKETPLACE"):
            snapshots = ChannelSnapshot.objects.filter(
                variant_id__in=base_variant_ids,
                channel=code,
            ).exclude(state="STALE")
            states = {
                row["state"] or "UNKNOWN": row["total"]
                for row in snapshots.values("state").annotate(
                    total=Count("variant_id", distinct=True),
                ).order_by("state")
            }
            created = snapshots.values("variant_id").distinct().count()
            return {
                "classification": classification,
                "created": created,
                "missing": max(base_variant_count - created, 0),
                "coverage_percent": round((created / base_variant_count * 100) if base_variant_count else 0, 1),
                "states": states,
            }

        shopify_states = {
            row["product__status"] or "UNKNOWN": row["total"]
            for row in base_variants.values("product__status").annotate(
                total=Count("id"),
            ).order_by("product__status")
        }
        sodimac_links = SodimacCatalogLink.objects.filter(
            variant_id__in=base_variant_ids,
            active=True,
        )
        sodimac_created = sodimac_links.filter(
            status=SodimacCatalogLink.Status.LINKED_EXACT,
        ).values("variant_id").distinct().count()
        sodimac_states = {
            row["status"]: row["total"]
            for row in sodimac_links.values("status").annotate(
                total=Count("variant_id", distinct=True),
            ).order_by("status")
        }
        siigo_exact_rows = SiigoProductSnapshot.objects.filter(
            matched_variant_id__in=base_variant_ids,
            match_status=SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY,
        )
        siigo_created = siigo_exact_rows.values("matched_variant_id").distinct().count()
        siigo_states = {
            "ACTIVE": siigo_exact_rows.filter(active=True).values("matched_variant_id").distinct().count(),
            "INACTIVE": siigo_exact_rows.filter(active=False).values("matched_variant_id").distinct().count(),
        }
        channel_summary = {
            "SHOPIFY": {
                "classification": "MASTER",
                "created": base_variant_count,
                "missing": 0,
                "coverage_percent": 100.0 if base_variant_count else 0,
                "states": shopify_states,
            },
            "SIIGO": {
                "classification": "COMMERCIAL_CATALOG_REQUIRED",
                "created": siigo_created,
                "missing": max(base_variant_count - siigo_created, 0),
                "coverage_percent": round((siigo_created / base_variant_count * 100) if base_variant_count else 0, 1),
                "states": siigo_states,
            },
            "MERCADO_LIBRE": channel_metrics(Channel.MERCADO_LIBRE),
            "FALABELLA": channel_metrics(Channel.FALABELLA),
            "MADECENTRO": channel_metrics(Channel.MADECENTRO, "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL"),
            "SODIMAC": {
                "classification": "FILE_CONFIRMED_LINK",
                "created": sodimac_created,
                "missing": max(base_variant_count - sodimac_created, 0),
                "coverage_percent": round((sodimac_created / base_variant_count * 100) if base_variant_count else 0, 1),
                "states": sodimac_states,
            },
            "RAPPI": {
                "classification": "NOT_CONNECTED",
                "created": 0,
                "missing": base_variant_count,
                "coverage_percent": 0,
                "states": {},
            },
        }
        baru_items = SkuReconciliation.objects.filter(
            supplier_item__provider__name__iexact="Barú",
        )
        net_cost_targets = base_variants.filter(
            canonical_cost__observation__derived_net_cost__isnull=False,
        )
        current_shopify_costs = base_variants.filter(
            cost_observations__source="SHOPIFY",
            cost_observations__raw_cost__isnull=False,
        ).distinct()
        summary = all_products.aggregate(products=Count("id"), avg_quality=Avg("quality_score"))
        summary.update({
            "variants": base_variant_count,
            "needs_review": all_products.filter(needs_review=True).count(),
            "missing_data": all_products.exclude(missing_fields=[]).count(),
            "external_writes": 0,
            "siigo_records": SiigoProductSnapshot.objects.count(),
            "siigo_without_shopify": SiigoProductSnapshot.objects.filter(match_status="MISSING_SHOPIFY").count(),
            "siigo_created_shopify": siigo_created,
            "siigo_missing_shopify": max(base_variant_count - siigo_created, 0),
            "supplier_catalog_rows": SupplierCatalogImport.objects.aggregate(total=Sum("extracted_rows"))["total"] or 0,
            "siigo_cost_records": SiigoProductSnapshot.objects.exclude(cost_status="NOT_PROVIDED_BY_PRODUCT_LIST").count(),
            "envia": {
                "package_complete_variants": len(envia_complete_variant_ids),
                "missing_package_variants": base_variants.exclude(id__in=envia_complete_variant_ids).count(),
                "ready_to_quote_variants": base_variants.filter(id__in=envia_complete_variant_ids - envia_quoted_variant_ids).count(),
                "current_quote_variants": base_variants.filter(id__in=envia_quoted_variant_ids).count(),
                "historical_realized_guides": LogisticsQuoteSnapshot.objects.filter(provider="ENVIA", basis=LogisticsQuoteSnapshot.Basis.REALIZED_GUIDE, amount__isnull=False).count(),
                "unlinked_historical_guides": LogisticsQuoteSnapshot.objects.filter(provider="ENVIA", basis=LogisticsQuoteSnapshot.Basis.REALIZED_GUIDE, amount__isnull=False, variant__isnull=True).count(),
            },
            "shipping": {
                "mercadolibre_seller_estimate_variants": len(meli_seller_variants),
                "mercadolibre_buyer_reference_variants": len(meli_buyer_variants),
                "metric": "P75_BY_ROUTE_PACKAGE_WAREHOUSE",
                "metric_status": "PENDING_LINKED_HISTORY",
            },
            "channel_coverage": {
                "SHOPIFY": channel_summary["SHOPIFY"]["created"],
                "SIIGO": siigo_created,
                "MERCADO_LIBRE": channel_summary["MERCADO_LIBRE"]["created"],
                "FALABELLA": channel_summary["FALABELLA"]["created"],
                "MADECENTRO": channel_summary["MADECENTRO"]["created"],
            },
            "master_catalog": {
                "basis": "SHOPIFY_VARIANT",
                "products": all_products.count(),
                "variants": base_variant_count,
                "excluded_supplier_only_products": MasterProduct.objects.exclude(
                    status="STALE_LOCAL_SNAPSHOT",
                ).filter(Q(shopify_product_id__isnull=True) | Q(shopify_product_id="")).count(),
                "channels": channel_summary,
                "baru_price_source": {
                    "role": "PRICE_REFERENCE_ONLY",
                    "rows": baru_items.count(),
                    "exact_shopify_links": baru_items.filter(status=SkuReconciliation.Status.EXACT).count(),
                    "outside_master": baru_items.exclude(status=SkuReconciliation.Status.EXACT).count(),
                },
                "shopify_cost": {
                    "tax_basis": "EXCLUDING_VAT",
                    "validated_net_targets": net_cost_targets.count(),
                    "pending_net_targets": base_variant_count - net_cost_targets.count(),
                    "current_shopify_values": current_shopify_costs.count(),
                    "current_values_tax_pending": current_shopify_costs.filter(
                        cost_observations__source="SHOPIFY",
                        cost_observations__tax_treatment="PENDING",
                    ).distinct().count(),
                },
            },
        })
        facets = {
            "providers": sorted(
                set(all_products.exclude(vendor="").values_list("vendor", flat=True)),
                key=str.casefold,
            ),
            "brands": sorted(
                set(all_products.exclude(brand="").values_list("brand", flat=True)),
                key=str.casefold,
            ),
            "categories": sorted(
                set(all_products.exclude(category="").values_list("category", flat=True)),
                key=str.casefold,
            ),
            "collections": sorted(
                {
                    collection_name
                    for collections in all_products.values_list("collections", flat=True)
                    for collection_name in (collections or [])
                    if collection_name
                },
                key=str.casefold,
            ),
        }
        latest_supplier_import = SupplierCatalogImport.objects.select_related("provider").first()
        external_ids = {
            str((snapshot.payload or {}).get("external_snapshot_id"))
            for variant in variants for snapshot in variant.channel_snapshots.all()
            if (snapshot.payload or {}).get("external_snapshot_id")
        }
        external_snapshots = {
            str(row.id): row for row in ExternalChannelProductSnapshot.objects.filter(id__in=external_ids)
        }
        average_shipping_reference = build_average_shipping_reference()
        serialized_variants = VariantSerializer(
            variants,
            many=True,
            context={
                "external_snapshots": external_snapshots,
                "average_shipping_reference": average_shipping_reference,
            },
        ).data
        products = []
        for variant, variant_payload in zip(variants, serialized_variants):
            product = variant.product
            products.append({
                "id": str(variant.id),
                "product_id": str(product.id),
                "shopify_product_id": product.shopify_product_id,
                "title": product.title,
                "vendor": product.vendor,
                "brand": product.brand,
                "category": product.category,
                "product_type": product.product_type,
                "status": product.status,
                "tags": product.tags,
                "collections": product.collections,
                "quality_score": product.quality_score,
                "missing_fields": product.missing_fields,
                "needs_review": product.needs_review,
                "updated_at": product.updated_at,
                "variants": [variant_payload],
                "images": [
                    {"source_url": image.source_url, "alt_text": image.alt_text, "position": image.position}
                    for image in product.images.all()
                ],
                "channel_snapshots": ChannelSnapshotSerializer(
                    variant.channel_snapshots.all(),
                    many=True,
                    context={"external_snapshots": external_snapshots},
                ).data,
            })
        payload = {
            "mode": "LOCAL_SIMULATION",
            "last_success_at": import_state.last_success_at,
            "summary": summary,
            "facets": facets,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_variants,
                "pages": max((total_variants + page_size - 1) // page_size, 1),
                "unit": "SHOPIFY_VARIANT",
                "filtered": any(key not in {"page", "page_size", "fresh"} for key in request.query_params.keys()),
            },
            "products": products,
            "providers": ProviderConfigSerializer(providers, many=True).data,
            "policies": PricingPolicySerializer(policies, many=True).data,
            "import_state": ImportStateSerializer(import_state).data,
            "supplier_import": SupplierCatalogImportSerializer(latest_supplier_import).data if latest_supplier_import else None,
            "reconciliations": ReconciliationSerializer(reconciliations, many=True).data,
            "missing_shopify": ReconciliationSerializer(missing_shopify, many=True).data,
            "history": HistorySerializer(history, many=True).data,
            "integration_statuses": IntegrationReadStatusSerializer(IntegrationReadStatus.objects.all(), many=True).data,
            "channels": [
                {"code": "SHOPIFY", "label": "Shopify", "phase": "local_snapshot", "connected": ChannelSnapshot.objects.filter(channel=Channel.SHOPIFY).exclude(state="STALE").exists()},
                {"code": "SIIGO", "label": "Siigo", "phase": "local_snapshot", "connected": SiigoProductSnapshot.objects.exists()},
                {"code": "MERCADO_LIBRE", "label": "Mercado Libre", "phase": "local_snapshot", "connected": ExternalChannelProductSnapshot.objects.filter(channel=Channel.MERCADO_LIBRE, active=True).exists()},
                {"code": "FALABELLA", "label": "Falabella", "phase": "local_snapshot", "connected": ExternalChannelProductSnapshot.objects.filter(channel=Channel.FALABELLA, active=True).exists()},
                {"code": "SODIMAC", "label": "Sodimac", "phase": "future", "connected": False},
                {"code": "MADECENTRO", "label": "Madecentro", "phase": "local_commercial_pilot", "connected": ExternalChannelProductSnapshot.objects.filter(channel=Channel.MADECENTRO, active=True).exists()},
                {"code": "RAPPI", "label": "Rappi", "phase": "future", "connected": False},
            ],
        }
        cache.set(cache_key, payload, timeout=45)
        response = Response(payload)
        response["X-Pamo-Cache"] = "MISS"
        response["Cache-Control"] = "private, max-age=30, stale-while-revalidate=300"
        return response


class CatalogColumnOptionsAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        column = request.query_params.get("column", "")
        if column not in CATALOG_COLUMN_KEYS:
            return Response({"detail": "Columna no soportada."}, status=400)

        base_variants = shopify_master_variants()
        direct_options = None
        if column == "SHOPIFY__status":
            direct_options = base_variants.exclude(
                product__status="",
            ).values_list("product__status", flat=True).distinct()
        elif column == "provider":
            direct_options = base_variants.exclude(
                product__vendor="",
            ).values_list("product__vendor", flat=True).distinct()
        elif column == "product":
            direct_options = base_variants.exclude(
                product__title="",
            ).values_list("product__title", flat=True).distinct()
        elif column == "sku":
            direct_options = list(base_variants.exclude(sku="").values_list("sku", flat=True).distinct())
            if base_variants.filter(Q(sku="") | Q(sku__isnull=True)).exists():
                direct_options.append("SKU pendiente")
        elif column == "photo":
            direct_options = []
            if base_variants.filter(product__images__source_url__gt="").exists():
                direct_options.append("Con imagen")
            if base_variants.exclude(product__images__source_url__gt="").exists():
                direct_options.append("Sin imagen")
        elif column == "siigo":
            linked_ids = SiigoProductSnapshot.objects.filter(
                match_status=SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY,
                matched_variant__isnull=False,
            ).values("matched_variant_id")
            direct_options = []
            if base_variants.filter(id__in=linked_ids).exists():
                direct_options.append("CREADO")
            if base_variants.exclude(id__in=linked_ids).exists():
                direct_options.append("FALTA CREAR")

        if direct_options is not None:
            options = sorted(
                {str(value) for value in direct_options if value is not None},
                key=lambda value: value.casefold(),
            )
            return Response({
                "column": column,
                "options": options,
                "total_options": len(options),
                "catalog_variants": base_variants.count(),
                "scope": "ALL_SHOPIFY_VARIANTS",
                "external_writes": 0,
            })

        index = catalog_column_index()
        options = sorted(
            {row["values"][column] for row in index.values()},
            key=lambda value: (value == "Pendiente", value.casefold()),
        )
        return Response({
            "column": column,
            "options": options,
            "total_options": len(options),
            "catalog_variants": len(index),
            "scope": "ALL_SHOPIFY_VARIANTS",
            "external_writes": 0,
        })


class ChannelAlignmentAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        channel = str(request.query_params.get("channel") or "MERCADO_LIBRE").upper()
        if channel not in {"SHOPIFY", "SIIGO", "MERCADO_LIBRE", "FALABELLA", "MADECENTRO"}:
            return Response({"detail": "Canal no soportado."}, status=400)
        page = max(int(request.query_params.get("page", 1)), 1)
        page_size = min(max(int(request.query_params.get("page_size", 50)), 25), 100)
        search = str(request.query_params.get("search") or "").strip()
        match_status = str(request.query_params.get("match_status") or "").strip()

        def external_metrics(code):
            rows = ExternalChannelProductSnapshot.objects.filter(channel=code, active=True)
            return {
                "total": rows.count(),
                "exact": rows.filter(match_status="EXACT_SKU").count(),
                "missing_shopify": rows.filter(match_status="MISSING_SHOPIFY").count(),
                "missing_sku": rows.filter(match_status="MISSING_SKU").count(),
                "ambiguous": rows.filter(match_status__in=["AMBIGUOUS_SKU", "IDENTIFIER_REVIEW"]).count(),
                "duplicates": rows.filter(match_status="DUPLICATE_SKU").count(),
            }

        shopify_rows = ProductVariant.objects.exclude(
            product__status="STALE_LOCAL_SNAPSHOT",
        ).exclude(
            product__shopify_product_id__isnull=True,
        ).exclude(
            product__shopify_product_id="",
        ).exclude(
            shopify_variant_id__isnull=True,
        ).exclude(
            shopify_variant_id="",
        )
        summary = {
            "SHOPIFY": {
                "total": shopify_rows.count(),
                "exact": shopify_rows.count(),
                "missing_shopify": 0, "missing_sku": shopify_rows.filter(sku="").count(), "ambiguous": 0, "duplicates": 0,
            },
            "SIIGO": {
                "total": SiigoProductSnapshot.objects.count(),
                "exact": SiigoProductSnapshot.objects.filter(match_status="EXACT_SHOPIFY").count(),
                "missing_shopify": SiigoProductSnapshot.objects.filter(match_status="MISSING_SHOPIFY").count(),
                "missing_sku": SiigoProductSnapshot.objects.filter(sku="").count(),
                "ambiguous": SiigoProductSnapshot.objects.filter(match_status="AMBIGUOUS_SHOPIFY").count(),
                "duplicates": 0,
            },
            "MERCADO_LIBRE": external_metrics(Channel.MERCADO_LIBRE),
            "FALABELLA": external_metrics(Channel.FALABELLA),
            "MADECENTRO": external_metrics(Channel.MADECENTRO),
        }

        offset = (page - 1) * page_size
        if channel in {"MERCADO_LIBRE", "FALABELLA", "MADECENTRO"}:
            rows = ExternalChannelProductSnapshot.objects.filter(channel=channel, active=True).select_related(
                "matched_variant__product",
            ).prefetch_related("matched_variant__product__images")
            if search:
                rows = rows.filter(Q(sku__icontains=search) | Q(title__icontains=search) | Q(external_product_id__icontains=search))
            if match_status:
                rows = rows.filter(match_status=match_status)
            total = rows.count()
            records = ExternalChannelProductSnapshotSerializer(rows[offset:offset + page_size], many=True).data
        elif channel == "SIIGO":
            rows = SiigoProductSnapshot.objects.select_related("matched_variant__product")
            if search:
                rows = rows.filter(Q(sku__icontains=search) | Q(name__icontains=search) | Q(siigo_id__icontains=search))
            if match_status:
                rows = rows.filter(match_status=match_status)
            total = rows.count()
            records = [{
                "id": row.siigo_id, "channel": "SIIGO", "external_product_id": row.siigo_id,
                "external_variant_id": "", "sku": row.sku, "barcode": "", "title": row.name,
                "brand": "", "category": "", "state": "ACTIVE" if row.active else "INACTIVE",
                "price": row.sale_price, "inventory_available": row.available_quantity, "currency": "COP",
                "url": "", "image_url": "", "match_status": row.match_status,
                "match_reason": "Conciliación exacta por SKU." if row.match_status == "EXACT_SHOPIFY" else "Requiere revisión contra Shopify.",
                "candidate_variant_ids": [], "matched_shopify_sku": row.matched_variant.sku if row.matched_variant else "",
                "matched_shopify_product": row.matched_variant.product.title if row.matched_variant else "",
                "observed_at": row.observed_at, "source_updated_at": row.source_updated_at, "active": row.active,
            } for row in rows[offset:offset + page_size]]
        else:
            rows = shopify_rows.select_related("product").prefetch_related("product__images")
            if search:
                rows = rows.filter(Q(sku__icontains=search) | Q(product__title__icontains=search) | Q(shopify_variant_id__icontains=search))
            total = rows.count()
            records = []
            for row in rows[offset:offset + page_size]:
                image = next(iter(row.product.images.all()), None)
                records.append({
                    "id": str(row.id), "channel": "SHOPIFY", "external_product_id": row.product.shopify_product_id,
                    "external_variant_id": row.shopify_variant_id, "sku": row.sku, "barcode": row.barcode,
                    "title": row.product.title, "brand": row.product.brand, "category": row.product.category,
                    "state": row.product.status, "price": row.price, "inventory_available": None, "currency": "COP",
                    "url": "", "image_url": image.source_url if image else "", "match_status": "MASTER",
                    "match_reason": "Fuente maestra Shopify.", "candidate_variant_ids": [],
                    "matched_shopify_sku": row.sku, "matched_shopify_product": row.product.title,
                    "observed_at": row.product.updated_at, "source_updated_at": row.product.updated_at, "active": True,
                })

        statuses = IntegrationReadStatus.objects.filter(system__in=["SHOPIFY", "SIIGO", "MERCADO_LIBRE", "FALABELLA", "MADECENTRO"])
        channel_context = {}
        if channel == Channel.MADECENTRO:
            sample = ExternalChannelProductSnapshot.objects.filter(
                channel=Channel.MADECENTRO,
                active=True,
            ).order_by("external_product_id").first()
            channel_context = {
                "classification": "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL",
                "label": "Propuesta comercial local; no demuestra publicación ni inventario en Madecentro.",
                "commercial_policy": (sample.payload or {}).get("commercial_policy", {}) if sample else {},
                "margin_warning": (sample.payload or {}).get("margin_warning", "") if sample else "",
            }
        return Response({
            "mode": "LOCAL_READONLY_ALIGNMENT", "external_writes": 0, "channel": channel,
            "summary": summary, "records": records,
            "channel_context": channel_context,
            "pagination": {"page": page, "page_size": page_size, "total": total, "pages": max((total + page_size - 1) // page_size, 1)},
            "integration_statuses": IntegrationReadStatusSerializer(statuses, many=True).data,
        })


class PricingSimulationAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def post(self, request):
        provider = ProviderConfig.objects.get(pk=request.data["provider_id"])
        policy = PricingPolicy.objects.get(pk=request.data["policy_id"])
        if policy.provider_id and policy.provider_id != provider.id:
            return Response({
                "detail": "La regla elegida pertenece a otro proveedor. Selecciona una regla compatible.",
                "code": "PRICING_POLICY_PROVIDER_MISMATCH",
            }, status=400)
        supplier_price = Decimal(str(request.data["supplier_price"]))
        quoted_shipping = Decimal(str(request.data.get("quoted_shipping", 0)))
        customer_charge = Decimal(str(request.data.get("customer_shipping_charge", 0)))
        adjustment = provider.sku_adjustments.filter(sku=request.data.get("sku", "")).first()
        try:
            result = calculate_price(
                provider=provider,
                supplier_price=supplier_price,
                policy=policy,
                quoted_shipping=quoted_shipping,
                customer_shipping_charge=customer_charge,
                sku_adjustment=adjustment,
            )
        except PricingInputError as error:
            return Response({"detail": str(error), "code": "PRICING_INPUT_INCOMPLETE"}, status=400)

        calculation = PriceCalculation.objects.create(
            channel=policy.channel or "SHOPIFY",
            policy=policy,
            input_snapshot=request.data,
            formula=result.formula,
            normalized_cost=result.normalized_cost,
            previous_price=request.data.get("previous_price"),
            proposed_price=result.proposed_price,
            achieved_margin_percent=result.achieved_margin_percent,
            commission_amount=result.commission_amount,
            logistics_reserve=policy.logistics_reserve or provider.logistics_reserve,
            quoted_shipping=quoted_shipping,
            customer_shipping_charge=customer_charge,
            shipping_subsidy=result.shipping_subsidy,
            rule_reason=result.reason,
        )
        return Response({
            "calculation_id": calculation.id,
            "normalized_cost": result.normalized_cost,
            "proposed_price": result.proposed_price,
            "achieved_margin_percent": result.achieved_margin_percent,
            "commission_amount": result.commission_amount,
            "shipping_subsidy": result.shipping_subsidy,
            "formula": result.formula,
            "reason": result.reason,
            "warnings": result.warnings,
            "policy_approval_status": policy.approval_status,
            "policy_active": policy.active,
            "execution_allowed": False,
            "shipping_options": shipping_options(
                provider=provider,
                supplier_price=supplier_price,
                policy=policy,
                quoted_shipping=quoted_shipping,
                sku_adjustment=adjustment,
                current_product_price=request.data.get("previous_price"),
                logistics_inputs_complete=True,
            ),
            "external_writes": 0,
        })


class ShopifyImportPlanAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        state = ShopifyImportState.objects.filter(key="PRIMARY").first()
        return Response({
            "status": "LOCAL_SNAPSHOT_IMPORTED" if state and state.status == "SUCCEEDED" else "DESIGNED_NOT_CONNECTED",
            "reads_enabled": SHOPIFY_READS_ENABLED,
            "external_writes_enabled": EXTERNAL_WRITES_ENABLED,
            "execution_allowed": False,
            "initial_import": {
                "pagination": "GraphQL cursor, pageInfo.hasNextPage/endCursor",
                "destination": "local MasterProduct/ProductVariant snapshots",
                "fields": ["product", "variant", "SKU", "price", "compare-at", "status", "vendor", "collections", "inventory by location", "images", "useful metafields", "publication quality"],
            },
            "incremental": {"cursor": "persisted locally", "updated_after": "persisted locally", "future_webhooks": "idempotent WebhookInbox"},
            "reconciliation": "exact SKU only; duplicates, missing and ambiguous matches stay in review",
            "write_gate": "preview -> batch approval -> auditable history -> explicit future execution -> rollback",
            "authentication": "Credenciales inyectadas en memoria por el entorno autorizado; no persistidas localmente.",
            "connector": "Admin GraphQL read-only, sin Shopify CLI ni telemetría del prompt.",
            "required_variable_names": ["SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"],
            "external_writes": 0,
        })


class HypothesisPolicyAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def post(self, request):
        policy = PricingPolicy.objects.get(pk=request.data["policy_id"], approval_status=PricingPolicy.ApprovalStatus.HYPOTHESIS)
        decimal_fields = {
            "target_margin_percent": (Decimal("0"), Decimal("100")),
            "minimum_margin_percent": (Decimal("0"), Decimal("100")),
            "channel_commission_percent": (Decimal("0"), Decimal("100")),
            "logistics_reserve": (Decimal("0"), None),
            "max_shipping_subsidy": (Decimal("0"), None),
        }
        try:
            for field, (minimum, maximum) in decimal_fields.items():
                if field not in request.data:
                    continue
                value = Decimal(str(request.data[field]))
                if value < minimum or (maximum is not None and value > maximum):
                    raise ValueError(field)
                setattr(policy, field, value)
            if "rounding_increment" in request.data:
                rounding = int(request.data["rounding_increment"])
                if rounding <= 0:
                    raise ValueError("rounding_increment")
                policy.rounding_increment = rounding
        except (ValueError, ArithmeticError):
            return Response({"code": "INVALID_HYPOTHESIS_VALUE", "detail": "Revisa porcentajes, topes y redondeo."}, status=400)
        policy.active = False
        policy.simulation_only = True
        policy.approval_status = PricingPolicy.ApprovalStatus.HYPOTHESIS
        policy.reserve_behavior = PricingPolicy.ReserveBehavior.CAP
        policy.save()
        return Response({"policy": PricingPolicySerializer(policy).data, "external_writes": 0, "execution_allowed": False})


class ExecutiveSimulationAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        calculations = PriceCalculation.objects.all()
        totals = calculations.aggregate(
            gmv=Sum("proposed_price"),
            cost=Sum("normalized_cost"),
            commissions=Sum("commission_amount"),
            quoted_shipping=Sum("quoted_shipping"),
            customer_shipping=Sum("customer_shipping_charge"),
            subsidy=Sum("shipping_subsidy"),
            reserve=Sum("logistics_reserve"),
        )
        totals = {key: value or Decimal("0") for key, value in totals.items()}
        totals["gross_profit"] = totals["gmv"] - totals["cost"]
        totals["net_profit_estimate"] = totals["gross_profit"] - totals["commissions"] - totals["subsidy"] - totals["reserve"]
        totals["real_margin_percent"] = (totals["net_profit_estimate"] / totals["gmv"] * 100) if totals["gmv"] else Decimal("0")
        return Response({
            "basis": "SIMULATED_CHECKOUT_ESTIMATE",
            "totals": totals,
            "realized": {"guide_cost": None, "returns": None, "adjustments": None, "net_profit": None},
            "warning": "La cotización de envío no garantiza utilidad. El realizado se concilia después de guía, entrega, devolución y ajustes.",
            "dimensions": ["period", "channel", "provider", "brand", "collection", "category", "sku", "status"],
            "alerts": {
                "low_margin": calculations.filter(achieved_margin_percent__lt=20).count(),
                "subsidy_exceeded": 0,
                "missing_data": MasterProduct.objects.exclude(missing_fields=[]).count(),
            },
        })


class BulkPilotSimulationAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        return Response(build_bulk_metrics(provider_name=request.query_params.get("provider", "Barú"), persist=False))


class PhysicalReviewQueueAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        provider_name = request.query_params.get("provider", "Barú")
        candidates = PhysicalEvidenceCandidate.objects.select_related(
            "variant__product", "supplier_item"
        ).prefetch_related("decisions").order_by("-confidence", "variant__sku")
        classification = request.query_params.get("classification")
        scope = request.query_params.get("scope")
        if classification:
            candidates = candidates.filter(classification=classification)
        if scope:
            candidates = candidates.filter(scope=scope)
        rows = []
        for candidate in candidates[:500]:
            latest = candidate.decisions.first()
            rows.append({
                "id": candidate.id, "sku": candidate.variant.sku if candidate.variant else candidate.supplier_item.supplier_sku,
                "product": candidate.variant.product.title if candidate.variant else candidate.supplier_item.description,
                "field": candidate.field, "scope": candidate.scope, "classification": candidate.classification,
                "value": candidate.normalized_value, "unit": candidate.normalized_unit,
                "source_type": candidate.source_type, "source_reference": candidate.source_reference,
                "source_url": candidate.source_url, "identifier": {
                    "type": candidate.matching_identifier_type, "value": candidate.matching_identifier_value,
                },
                "evidence_excerpt": candidate.evidence_excerpt, "selector": candidate.evidence_selector,
                "confidence": candidate.confidence, "conflict": candidate.conflict,
                "conflict_details": candidate.conflict_details, "observed_at": candidate.observed_at,
                "stale_after": candidate.stale_after, "latest_decision": latest.action if latest else "PENDING",
                "shipping_impact": "No habilita cotización" if candidate.scope != "PACKAGE" or candidate.classification != "CONFIRMED" else "Requiere conjunto completo y aprobación local",
            })
        selections = PhysicalEnrichmentPilotSelection.objects.select_related("variant__product").all()
        previews = ShopifyPhysicalUpdatePreview.objects.select_related("variant").all()
        return Response({
            "rows": rows,
            "summary": {
                "candidates": PhysicalEvidenceCandidate.objects.count(),
                "confirmed_package": PhysicalEvidenceCandidate.objects.filter(scope="PACKAGE", classification="CONFIRMED", conflict=False).count(),
                "derived": PhysicalEvidenceCandidate.objects.filter(classification="DERIVED").count(),
                "estimated": PhysicalEvidenceCandidate.objects.filter(classification="ESTIMATED").count(),
                "conflicts": PhysicalEvidenceCandidate.objects.filter(conflict=True).count(),
                "pending_review": PhysicalEvidenceCandidate.objects.filter(decisions__isnull=True).count(),
                "shopify_previews_ready": previews.filter(status="READY_LOCAL").count(),
                "shopify_zero_weight_exact": SkuReconciliation.objects.filter(
                    supplier_item__provider__name=provider_name,
                    status="EXACT",
                    variant__channel_snapshots__channel="SHOPIFY",
                    variant__channel_snapshots__payload__weight__value=0,
                ).distinct().count(),
            },
            "pilot_selection": [{
                "rank": row.rank, "sku": row.variant.sku, "title": row.variant.product.title,
                "score": row.score, "criteria": row.criteria,
            } for row in selections],
            "shopify_previews": [{
                "sku": row.variant.sku, "status": row.status, "previous_values": row.previous_values,
                "proposed_metafields": row.proposed_metafields, "evidence": row.evidence_snapshot,
                "blockers": row.blockers, "rollback": row.rollback_payload, "external_writes": row.external_writes,
            } for row in previews],
            "rules": {
                "eligibility": "Solo PACKAGE confirmado y aprobado; PRODUCT o ESTIMATED nunca habilitan cotización ni actualización Shopify.",
                "actions": ["APPROVE_LOCAL", "REJECT", "REQUEST_PROVIDER"],
            },
            "external_writes": 0,
        })

    def post(self, request):
        candidate = get_object_or_404(
            PhysicalEvidenceCandidate.objects.select_related("variant"),
            pk=request.data.get("candidate_id"),
        )
        action = request.data.get("action")
        if action not in PhysicalEvidenceDecision.Action.values:
            return Response({"code": "INVALID_DECISION"}, status=400)
        if action == "APPROVE_LOCAL" and candidate.classification == "ESTIMATED":
            return Response({"code": "ESTIMATE_CANNOT_BE_APPROVED_FOR_SHIPPING", "detail": "Un producto similar solo genera una tarea de validación."}, status=400)
        if action == "APPROVE_LOCAL" and candidate.conflict:
            return Response({"code": "CONFLICT_REQUIRES_RESOLUTION", "detail": "Resuelve el conflicto de fuentes antes de aprobar."}, status=400)
        decision = PhysicalEvidenceDecision.objects.create(
            candidate=candidate, action=action, reason=str(request.data.get("reason") or "Decisión local")[:500],
            actor_label=str(request.data.get("actor_label") or "local-operator")[:160],
            decision_snapshot={"candidate_fingerprint": candidate.content_fingerprint, "classification": candidate.classification, "scope": candidate.scope},
            external_writes=0,
        )
        preview = build_shopify_preview(candidate.variant) if candidate.variant else None
        return Response({
            "decision_id": decision.id, "action": decision.action,
            "preview_status": preview.status if preview else "NOT_APPLICABLE",
            "shipping_eligible_evidence": candidate.classification == "CONFIRMED" and candidate.scope == "PACKAGE",
            "execution_allowed": False, "external_writes": 0,
        })


class PhysicalMeasurementTemplateAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        path = Path(__file__).resolve().parent / "assets" / "plantilla_baru_medidas_paquete_25_sku.xlsx"
        return FileResponse(path.open("rb"), as_attachment=True, filename="PLANTILLA_BARU_MEDIDAS_PAQUETE_25_SKU.xlsx")


class PhysicalMeasurementWorkspaceAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        return Response(measurement_workspace(request.query_params.get("provider", "Barú")))

    def post(self, request):
        action = str(request.data.get("action") or "")
        actor_label = str(request.data.get("actor_label") or "local-operator")[:160]
        try:
            if action == "CREATE_TASK":
                task = create_measurement_task(
                    request.data.get("variant_id"), request.data.get("task_action"), actor_label, request.data.get("note"),
                )
                return Response({"task_id": task.id, "status": task.status, "external_writes": 0})
            if action == "PREVIEW_IMPORT":
                uploaded = request.FILES.get("file")
                if not uploaded:
                    raise PhysicalMeasurementImportError("Seleccione un archivo XLSX o CSV.")
                if uploaded.size > 5 * 1024 * 1024:
                    raise PhysicalMeasurementImportError("El archivo supera el límite local de 5 MB.")
                batch = preview_measurement_import(
                    str(request.data.get("provider") or "Barú"), uploaded.name, uploaded.read(),
                )
                return Response({"batch": serialize_import_batch(batch), "execution_allowed_external": False, "external_writes": 0})
            if action == "APPLY_IMPORT_LOCAL":
                batch = apply_measurement_import(request.data.get("batch_id"), actor_label)
                return Response({"batch": serialize_import_batch(batch), "execution_allowed_external": False, "external_writes": 0})
            if action == "REVERSE_IMPORT_LOCAL":
                batch = reverse_measurement_import(request.data.get("batch_id"), actor_label)
                return Response({"batch": serialize_import_batch(batch), "execution_allowed_external": False, "external_writes": 0})
            if action == "REGISTER_MEASUREMENT":
                row = dict(request.data.get("measurement") or {})
                headers = [
                    "SKU", "GTIN", "Descripción", "Proveedor", "Peso empacado", "Unidad peso", "Largo paquete",
                    "Ancho paquete", "Alto paquete", "Unidad dimensiones", "Cantidad bultos", "Fecha verificación",
                    "Responsable", "Tipo de fuente", "Fuente / referencia", "Evidencia / foto (URL)", "Observaciones",
                ]
                values = [
                    row.get("sku"), row.get("gtin"), row.get("description"), row.get("provider", "Barú"), row.get("weight"),
                    row.get("weight_unit"), row.get("length"), row.get("width"), row.get("height"), row.get("dimension_unit"),
                    row.get("package_count"), row.get("verified_date"), row.get("responsible"), row.get("source_kind"),
                    row.get("source_reference"), row.get("evidence_url"), row.get("notes"),
                ]
                import csv
                import io
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(headers)
                writer.writerow(values)
                content = output.getvalue().encode("utf-8")
                batch = preview_measurement_import("Barú", f"medicion-{row.get('sku') or 'sin-sku'}.csv", content)
                return Response({"batch": serialize_import_batch(batch), "execution_allowed_external": False, "external_writes": 0})
            raise PhysicalMeasurementImportError("Acción de captura no permitida.")
        except (PhysicalMeasurementImportError, PhysicalMeasurementImportBatch.DoesNotExist, ValueError) as error:
            return Response({"code": "PHYSICAL_MEASUREMENT_ERROR", "detail": str(error), "external_writes": 0}, status=400)


class EnviaQuoteContractAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        return Response({
            "status": "FIXTURE_ONLY_NO_EXTERNAL_CALL",
            "request_contract": {
                "destination": ["city", "state", "country", "postal_code_prefix"],
                "package": ["length", "width", "height", "weight", "units", "scope=PACKAGE", "evidence_classification=CONFIRMED", "evidence candidate_id + APPROVE_LOCAL por campo"],
            },
            "forbidden_effects": ["guide", "label", "purchase", "dispatch"],
            "external_writes": 0,
        })

    def post(self, request):
        fixture_path = Path(__file__).resolve().parent / "contracts" / "envia_quote_fixture_v1.json"
        fixture = json.loads(fixture_path.read_text())
        try:
            run = run_fixture_quote(request.data, fixture)
        except EnviaQuoteContractError as error:
            return Response({"code": "QUOTE_CONTRACT_REJECTED", "detail": str(error), "external_writes": 0}, status=400)
        return Response({
            "run_id": run.id, "status": run.status, "response": run.response_snapshot,
            "execution_allowed": False, "external_writes": 0,
        })


class Phase6WorkspaceAPI(APIView):
    """Resumen local compartido por configurador y simulador."""

    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        batches = PricingLocalBatch.objects.all()[:20]
        simulations = MultwarehouseSimulationRun.objects.all()[:20]
        master_products = MasterProduct.objects.exclude(status="STALE_LOCAL_SNAPSHOT")
        collections = sorted({
            str(collection).strip()
            for values in master_products.values_list("collections", flat=True)
            for collection in (values or [])
            if str(collection).strip()
        }, key=str.casefold)
        scope_options = {
            "channels": [{"value": value, "label": label} for value, label in Channel.choices],
            "providers": list(master_products.exclude(vendor="").order_by("vendor").values_list("vendor", flat=True).distinct()),
            "brands": list(master_products.exclude(brand="").order_by("brand").values_list("brand", flat=True).distinct()),
            "collections": collections,
            "categories": list(master_products.exclude(category="").order_by("category").values_list("category", flat=True).distinct()),
            "product_types": list(master_products.exclude(product_type="").order_by("product_type").values_list("product_type", flat=True).distinct()),
        }
        return Response({
            "mode": "STRICT_LOCAL_PHASE_6",
            "pricing": {
                "rules": [serialize_rule(policy) for policy in PricingPolicy.objects.all()],
                "batches": [serialize_batch(batch) for batch in batches],
                "formula_contract": {
                    "MARKUP_PERCENT": "costo × (1 + markup %)",
                    "FIXED_INCREMENT": "costo + incremento COP",
                    "GROSS_MARGIN": "costo ÷ (1 - margen bruto %)",
                    "protected_floor": "(costo + cargos fijos + gastos sobre costo + subsidio usado) ÷ (1 - comisión - pago - administración - logística - otros gastos - margen mínimo)",
                    "final_price": "máximo(candidato, piso protegido), luego redondeo hacia arriba",
                    "reserve": "tope de protección; COP 0 añadidos automáticamente",
                },
                "precedence": ["SKU exacto", "canal + bodega", "colección/categoría/marca/proveedor", "global"],
                "rounding_choices": [100, 500, 1000],
                "scope_options": scope_options,
            },
            "logistics": {
                "runs": [{
                    "id": str(run.id), "status": run.status, "quote_basis": run.quote_basis,
                    "created_at": run.created_at, "external_writes": run.external_writes,
                } for run in simulations],
                "demo_cases": ["ONE_ORIGIN", "MULTIPLE_ORIGINS", "INSUFFICIENT_STOCK", "UNKNOWN_WAREHOUSE", "NOT_QUOTABLE", "SHIPPING_BREAKS_MARGIN", "TIE"],
                "contract": {
                    "sku_splitting": False, "one_guide_per_origin": True,
                    "quote_provider": "fixture local únicamente", "current_rate": False,
                    "package_unknown": "NO_COTIZABLE", "demo_commercially_eligible": False,
                },
            },
            "safety": {
                "database": "SQLite local" if connection.vendor == "sqlite" else "PostgreSQL aislado de Prototipos",
                "external_writes": 0, "execution_allowed_external": False,
                "shopify": "UNCHANGED", "envia": "DISCONNECTED_NO_GUIDES", "siigo": "UNCHANGED",
            },
        })


class Phase6PricingAPI(APIView):
    """Crea reglas y lotes únicamente en SQLite; nunca invoca un canal."""

    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def post(self, request):
        action = str(request.data.get("action") or "PREVIEW").upper()
        actor = str(request.data.get("actor_label") or "local-operator")[:160]
        try:
            if action == "SAVE_RULE_LOCAL":
                rule = save_rule(dict(request.data.get("rule") or {}))
                return Response({"rule": rule, "execution_allowed_external": False, "external_writes": 0})
            if action == "PREVIEW":
                batch, preview = create_pricing_preview(
                    dict(request.data.get("selection") or {}),
                    draft_rule=dict(request.data.get("rule") or {}) or None,
                    actor_label=actor,
                    include_saved=bool(request.data.get("include_saved_rules", True)),
                )
                return Response({"batch": serialize_batch(batch), "preview": preview, "external_writes": 0})
            if action == "APPLY_LOCAL":
                batch = apply_pricing_batch(request.data.get("batch_id"), actor)
                return Response({"batch": serialize_batch(batch, include_preview=True), "external_writes": 0})
            if action == "REVERSE_LOCAL":
                batch = reverse_pricing_batch(request.data.get("batch_id"), actor)
                return Response({"batch": serialize_batch(batch, include_preview=True), "external_writes": 0})
            raise Phase6InputError("Acción de precios no permitida.")
        except (Phase6InputError, PricingLocalBatch.DoesNotExist, PricingPolicy.DoesNotExist, ValueError) as error:
            return Response({"code": "PHASE6_PRICING_BLOCKED", "detail": str(error), "external_writes": 0}, status=400)


class Phase6MultwarehouseAPI(APIView):
    """Simula asignación por origen con inventario local y tarifa fixture marcada."""

    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def post(self, request):
        actor = str(request.data.get("actor_label") or "local-operator")[:160]
        try:
            run, result = run_multwarehouse(dict(request.data), actor)
        except (Phase6InputError, ValueError) as error:
            return Response({"code": "PHASE6_LOGISTICS_BLOCKED", "detail": str(error), "external_writes": 0}, status=400)
        return Response({
            "run_id": str(run.id), "result": result,
            "fixture_only": True, "current_rate": False, "guide_creation_allowed": False,
            "external_writes": 0,
        })


class Phase7WorkspaceAPI(APIView):
    """Perfiles estimados y matriz multicanal derivados solo de SQLite local."""

    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        return Response(build_phase7_workspace())


class SodimacCatalogWorkspaceAPI(APIView):
    permission_classes = [LocalOrAuthenticatedCatalogAccess]

    def get(self, request):
        filters = {key: request.query_params.get(key, "").strip() for key in (
            "link_status", "quality", "freshness", "missing", "provider", "warehouse", "inventory",
        )}
        return Response(build_sodimac_workspace(filters))

    def post(self, request):
        action = request.data.get("action")
        actor = str(request.data.get("actor_label") or "local-operator")[:160]
        try:
            if action in {"PREVIEW_IMPORT", "LOAD_DEMO_FIXTURE"}:
                is_fixture = action == "LOAD_DEMO_FIXTURE"
                if is_fixture:
                    source = Path(__file__).resolve().parent / "demo_data" / "sodimac_catalog_demo.csv"
                    filename, content = source.name, source.read_bytes()
                    mapping = None
                else:
                    upload = request.FILES.get("file")
                    if not upload:
                        raise SodimacCatalogError("Seleccione un archivo CSV o XLSX.")
                    filename, content = upload.name, upload.read()
                    raw_mapping = request.data.get("header_mapping")
                    mapping = json.loads(raw_mapping) if isinstance(raw_mapping, str) else raw_mapping
                batch = preview_sodimac_import(filename, content, mapping, actor, is_fixture)
                return Response({"batch": serialize_sodimac_batch(batch, include_rows=True), "external_writes": 0})
            if action == "APPLY_IMPORT_LOCAL":
                batch = apply_sodimac_import(request.data.get("batch_id"), actor)
                return Response({"batch": serialize_sodimac_batch(batch, include_rows=True), "external_writes": 0})
            if action == "REVERSE_IMPORT_LOCAL":
                batch = reverse_sodimac_import(request.data.get("batch_id"), actor)
                return Response({"batch": serialize_sodimac_batch(batch, include_rows=True), "external_writes": 0})
            if action == "REVERSE_KIT_IMPORT_LOCAL":
                batch = reverse_sodimac_kit_import(request.data.get("batch_id"), actor)
                return Response({
                    "kit_batch": {
                        "id": str(batch.id), "status": batch.status,
                        "kit_count": batch.kit_count, "component_rows": batch.component_rows,
                    },
                    "external_writes": 0,
                })
            if action == "ENQUEUE_INCREMENTAL_LOCAL":
                created = enqueue_incremental_audits(actor)
                return Response({"created": created, "scheduler": "NOT_CONFIGURED", "network_calls": 0, "external_writes": 0})
            raise SodimacCatalogError("Acción Sodimac no permitida.")
        except (SodimacCatalogError, ValueError, json.JSONDecodeError) as error:
            return Response({"code": "SODIMAC_CATALOG_BLOCKED", "detail": str(error), "external_writes": 0}, status=400)
