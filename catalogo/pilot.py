from collections import Counter
from decimal import Decimal
import json

from django.db.models import Q
from django.utils import timezone

from .models import (
    BulkSimulationRun,
    CanonicalCostSelection,
    ChannelSnapshot,
    CostObservation,
    IntegrationReadStatus,
    InventoryLevel,
    LogisticsQuoteSnapshot,
    MasterProduct,
    PricingPolicy,
    ProductMetafield,
    ProductVariant,
    PhysicalEvidenceCandidate,
    SiigoProductSnapshot,
    SkuReconciliation,
    SupplierCatalogItem,
)
from .pricing import commercial_sensitivity, select_policy, shipping_options


def _set_status(system, capability, status, message, *, count=None, evidence="", details=None, last_success_at=None):
    return IntegrationReadStatus.objects.update_or_create(
        system=system,
        capability=capability,
        defaults={
            "status": status,
            "message": message,
            "record_count": count,
            "evidence_reference": evidence,
            "details": details or {},
            "observed_at": timezone.now(),
            "last_success_at": last_success_at,
            "external_writes": 0,
        },
    )[0]


def refresh_read_statuses():
    shopify_products = MasterProduct.objects.exclude(shopify_product_id__isnull=True).exclude(shopify_product_id="")
    shopify_variants = ProductVariant.objects.exclude(shopify_variant_id="")
    last_shopify = ChannelSnapshot.objects.filter(channel="SHOPIFY").order_by("-observed_at").values_list("observed_at", flat=True).first()
    collection_count = shopify_products.exclude(collections=[]).count()
    compare_at_count = shopify_variants.exclude(compare_at_price__isnull=True).count()
    shopify_cost_count = CostObservation.objects.filter(source=CostObservation.Source.SHOPIFY, raw_cost__isnull=False).count()
    location_count = InventoryLevel.objects.count()
    metafield_count = ProductMetafield.objects.count()
    package_evidence_count = PhysicalEvidenceCandidate.objects.filter(scope="PACKAGE", classification="CONFIRMED", conflict=False).count()
    public_exact_count = PhysicalEvidenceCandidate.objects.filter(source_type__in=["MANUFACTURER", "PUBLIC_RETAIL_EXACT"]).count()
    siigo_count = SiigoProductSnapshot.objects.count()
    siigo_cost_count = CostObservation.objects.filter(source=CostObservation.Source.SIIGO, raw_cost__isnull=False).count()
    siigo_warehouse_count = SiigoProductSnapshot.objects.exclude(warehouses=[]).count()
    quote_count = LogisticsQuoteSnapshot.objects.filter(provider="ENVIA", basis="CHECKOUT_ESTIMATE", amount__isnull=False).count()
    guide_count = LogisticsQuoteSnapshot.objects.filter(provider="ENVIA", basis="REALIZED_GUIDE", amount__isnull=False).count()

    statuses = [
        _set_status("SHOPIFY", "catalog_snapshot", "AVAILABLE", "Snapshot local persistido; la UI no consulta Shopify en vivo.", count=shopify_products.count(), evidence="Local ChannelSnapshot", last_success_at=last_shopify),
        _set_status("SHOPIFY", "compare_at_price", "PARTIAL" if compare_at_count else "MISSING", "Disponible solo donde el snapshot expuso compare-at.", count=compare_at_count, evidence="ProductVariant.compare_at_price", last_success_at=last_shopify),
        _set_status("SHOPIFY", "collections", "PARTIAL" if collection_count else "MISSING", "Colecciones conservadas solo donde hubo evidencia en el snapshot.", count=collection_count, evidence="MasterProduct.collections", last_success_at=last_shopify),
        _set_status("SHOPIFY", "variant_unit_cost", "PARTIAL" if shopify_cost_count else "MISSING", "No se sustituye con precio de venta cuando unitCost está ausente.", count=shopify_cost_count, evidence="CostObservation.SHOPIFY", last_success_at=last_shopify),
        _set_status("SHOPIFY", "inventory_by_location", "PARTIAL" if location_count else "MISSING", "El total publicable usa inventoryQuantity; el snapshot conserva al menos una ubicación por variante, pero no afirma desglose completo cuando la conexión anidada está truncada.", count=location_count, evidence="InventoryLevel + inventoryQuantity", last_success_at=last_shopify, details={"nested_page_size": 1, "aggregate_preserved": True}),
        _set_status("SHOPIFY", "metafields", "PARTIAL" if metafield_count else "MISSING", "Metacampos conservados únicamente donde el snapshot los expuso." if metafield_count else "No hay metacampos verificables en el snapshot actual.", count=metafield_count, evidence="ProductMetafield", last_success_at=last_shopify),
        _set_status("LOGISTICS", "confirmed_package_evidence", "PARTIAL" if package_evidence_count else "MISSING", "Solo evidencia PACKAGE confirmada puede participar en cotización; PRODUCT y ESTIMATED quedan fuera.", count=package_evidence_count, evidence="PhysicalEvidenceCandidate"),
        _set_status("PUBLIC", "exact_physical_evidence", "PARTIAL" if public_exact_count else "MISSING", "Solo coincidencias exactas por SKU/GTIN/MPN se conservan como evidencia derivada; no confirman empaque por sí solas.", count=public_exact_count, evidence="Sanitized public evidence fixture"),
        _set_status("SHOPIFY", "read_only_connector", "AVAILABLE" if last_shopify else "BLOCKED", "La lectura usa Admin GraphQL con credenciales inyectadas en memoria; no usa Shopify CLI ni telemetría del texto de la tarea." if last_shopify else "Todavía no existe un snapshot importado por la ruta segura.", evidence="Sanitized Shopify Admin GraphQL snapshot", count=shopify_variants.count(), last_success_at=last_shopify, details={"secret_exposure": 0, "externalWrites": 0}),
        _set_status("SIIGO", "product_snapshot", "AVAILABLE" if siigo_count else "MISSING", "Productos persistidos desde una lectura autorizada previa.", count=siigo_count, evidence="SiigoProductSnapshot"),
        _set_status("SIIGO", "verified_cost", "PARTIAL" if siigo_cost_count else "MISSING", "El precio de lista nunca se reinterpreta como costo.", count=siigo_cost_count, evidence="CostObservation.SIIGO"),
        _set_status("SIIGO", "inventory_by_warehouse", "PARTIAL" if siigo_warehouse_count else "MISSING", "Existencias solo donde la respuesta identificó bodega y cantidad.", count=siigo_warehouse_count, evidence="SiigoProductSnapshot.warehouses"),
        _set_status("ENVIA", "checkout_quote", "AVAILABLE" if quote_count else "BLOCKED", "Sin cotización local verificable; las modalidades de cobro quedan bloqueadas." if not quote_count else "Cotizaciones de solo lectura persistidas localmente.", count=quote_count, evidence="LogisticsQuoteSnapshot.CHECKOUT_ESTIMATE"),
        _set_status("ENVIA", "non_binding_quote_contract", "AVAILABLE", "Contrato local validado con fixture; no crea guía, etiqueta, compra ni despacho.", count=0, evidence="envia_quote_fixture_v1.json", details={"binding": False, "guide_created": False, "externalWrites": 0}),
        _set_status("ENVIA", "realized_guide_cost", "AVAILABLE" if guide_count else "BLOCKED", "Sin costo real de guía autorizado/importado para conciliación." if not guide_count else "Costos realizados persistidos localmente.", count=guide_count, evidence="LogisticsQuoteSnapshot.REALIZED_GUIDE"),
        _set_status("EXTERNAL", "writes", "AVAILABLE", "Todas las integraciones externas permanecen en cero escrituras.", count=0, evidence="EXTERNAL_WRITES_ENABLED=False", details={"externalWrites": 0}),
    ]
    return statuses


def build_bulk_metrics(provider_name="Barú", persist=False):
    items = SupplierCatalogItem.objects.filter(provider__name=provider_name).select_related("provider").prefetch_related(
        "inventory_snapshots", "reconciliations__variant__channel_snapshots",
        "reconciliations__variant__inventory_sources", "reconciliations__variant__logistics_quotes",
        "reconciliations__variant__physical_candidates__decisions",
    )
    item_count = items.count()
    cost_covered = items.exclude(supplier_price__isnull=True).count()
    exact_matches = SkuReconciliation.objects.filter(supplier_item__provider__name=provider_name, status="EXACT").count()
    ambiguous = SkuReconciliation.objects.filter(supplier_item__provider__name=provider_name, status__in=["DUPLICATE", "AMBIGUOUS"]).count()
    catalog_only = SkuReconciliation.objects.filter(supplier_item__provider__name=provider_name, status="MISSING").count()
    provider = items.first().provider if item_count else None
    policies = list(PricingPolicy.objects.filter(active=True, approval_status=PricingPolicy.ApprovalStatus.APPROVED_LOCAL).select_related("provider"))
    hypothesis = PricingPolicy.objects.filter(provider=provider, approval_status=PricingPolicy.ApprovalStatus.HYPOTHESIS).order_by("-updated_at").first() if provider else None
    rule_counts = Counter()
    simulated = 0
    low_margin = 0
    subsidy_exceeded = 0
    eligible = {"REAL_RATE": 0, "3000": 0, "2000": 0, "0": 0}

    evidence_counts = Counter()
    missing_rows = []
    commercial_ready = 0
    data_ready = 0
    for item in items:
        match = next((row for row in item.reconciliations.all() if row.status == "EXACT" and row.variant_id), None)
        variant = match.variant if match else None
        shopify_snapshot = None
        if variant:
            shopify_snapshot = next((row for row in variant.channel_snapshots.all() if row.channel == "SHOPIFY"), None)
        approved_fields = set()
        if variant:
            for candidate in variant.physical_candidates.all():
                latest_decision = candidate.decisions.all()[0] if candidate.decisions.all() else None
                if (
                    candidate.scope == PhysicalEvidenceCandidate.Scope.PACKAGE
                    and candidate.classification == PhysicalEvidenceCandidate.Classification.CONFIRMED
                    and not candidate.conflict
                    and (not candidate.stale_after or candidate.stale_after > timezone.now())
                    and latest_decision and latest_decision.action == "APPROVE_LOCAL"
                    and (not latest_decision.expires_at or latest_decision.expires_at > timezone.now())
                ):
                    approved_fields.add(candidate.field)
        weight_known = "WEIGHT" in approved_fields
        dimensions_known = {"LENGTH", "WIDTH", "HEIGHT"}.issubset(approved_fields)
        supplier_inventory_known = bool(item.inventory_snapshots.all()) or item.inventory is not None
        channel_inventory_known = bool(variant and any(not row.stock_unknown for row in variant.inventory_sources.all()))
        inventory_known = supplier_inventory_known or channel_inventory_known
        current_quote_known = bool(variant and any(
            row.basis == "CHECKOUT_ESTIMATE" and row.amount is not None for row in variant.logistics_quotes.all()
        ))
        exact = match is not None
        cost_known = item.supplier_price is not None
        blockers = []
        if not exact: blockers.append("shopify_exact_match")
        if not cost_known: blockers.append("verified_cost")
        if not weight_known: blockers.append("weight")
        if not dimensions_known: blockers.append("dimensions")
        if not inventory_known: blockers.append("inventory")
        if not policies: blockers.append("approved_policy")
        if not current_quote_known: blockers.append("current_envia_quote")
        evidence_counts["weight_confirmed"] += int(weight_known)
        evidence_counts["dimensions_confirmed"] += int(dimensions_known)
        evidence_counts["inventory_confirmed"] += int(inventory_known)
        if exact and cost_known and weight_known and dimensions_known and inventory_known:
            data_ready += 1
        if not blockers:
            commercial_ready += 1
        if blockers and len(missing_rows) < 250:
            missing_rows.append({"sku": item.supplier_sku, "blockers": blockers, "shopify_match": "EXACT" if exact else "MISSING"})

    missing_weight = item_count - evidence_counts["weight_confirmed"]
    missing_dimensions = item_count - evidence_counts["dimensions_confirmed"]
    missing_inventory = item_count - evidence_counts["inventory_confirmed"]

    if provider:
        selections = CanonicalCostSelection.objects.filter(
            observation__provider=provider,
            observation__raw_cost__isnull=False,
        ).select_related("variant__product", "observation")
        for selection in selections.iterator():
            variant = selection.variant
            product = variant.product
            context = {
                "channel": "SHOPIFY", "provider_id": provider.id,
                "collection": (product.collections or [""])[0] if product.collections else "",
                "brand": product.brand, "category": product.category,
                "product_type": product.product_type, "sku": variant.sku,
            }
            policy = select_policy(policies, context)
            if not policy:
                rule_counts["SIN_REGLA_APROBADA"] += 1
                continue
            rule_counts[policy.name] += 1
            simulated += 1
            options = shipping_options(
                provider=provider,
                supplier_price=selection.observation.raw_cost,
                policy=policy,
                quoted_shipping=None,
                current_product_price=variant.price,
                logistics_inputs_complete=False,
            )
            for option in options:
                if option["supported"]:
                    eligible[str(option["customer_charge"])] += 1
                if any("tope" in warning for warning in option["warnings"]):
                    subsidy_exceeded += 1

    guide_amounts = sorted(LogisticsQuoteSnapshot.objects.filter(basis="REALIZED_GUIDE", amount__isnull=False).values_list("amount", flat=True))
    historical_guide_median = guide_amounts[len(guide_amounts) // 2] if guide_amounts else None
    catalog_costs = sorted(items.exclude(supplier_price__isnull=True).values_list("supplier_price", flat=True))
    representative_cost = catalog_costs[len(catalog_costs) // 2] if catalog_costs else None
    sensitivity = []
    if hypothesis and representative_cost is not None and historical_guide_median is not None:
        sensitivity = commercial_sensitivity(
            provider=provider, supplier_price=representative_cost, policy=hypothesis,
            quoted_shipping=historical_guide_median,
        )

    metrics = {
        "provider": provider_name,
        "catalog_rows": item_count,
        "cost_coverage": {"known": cost_covered, "unknown": item_count - cost_covered, "percent": round(cost_covered * 100 / item_count, 2) if item_count else 0},
        "shopify_reconciliation": {"exact": exact_matches, "catalog_only": catalog_only, "ambiguous_or_duplicate": ambiguous},
        "missing_physical_data": {"weight": missing_weight, "dimensions": missing_dimensions},
        "inventory": {"unknown": missing_inventory, "known": item_count - missing_inventory},
        "pricing": {"simulated": simulated, "without_applicable_rule": sum(count for name, count in rule_counts.items() if name == "SIN_REGLA_APROBADA"), "by_rule": dict(rule_counts), "low_margin": low_margin},
        "shipping": {
            "quote_coverage": LogisticsQuoteSnapshot.objects.filter(basis="CHECKOUT_ESTIMATE", amount__isnull=False).count(),
            "historical_realized_guides": len(guide_amounts), "historical_realized_median": historical_guide_median,
            "subsidy_exceeded": subsidy_exceeded, "eligible": eligible,
            "blocked_missing_logistics_inputs": item_count - data_ready,
            "blocked_missing_current_quote": item_count,
        },
        "readiness": {
            "data_ready_count": data_ready,
            "data_ready_percent": round(data_ready * 100 / item_count, 2) if item_count else 0,
            "commercial_ready_count": commercial_ready,
            "commercial_ready_percent": round(commercial_ready * 100 / item_count, 2) if item_count else 0,
            "pilot_decision": "GO" if commercial_ready == item_count and item_count else "BLOCKED",
            "definition": "Costo + SKU Shopify exacto + peso y dimensiones de PAQUETE confirmados y aprobados localmente + inventario + política aprobada + cotización Envía actual.",
        },
        "evidence_classification": {
            "confirmed": {"cost": cost_covered, "weight": evidence_counts["weight_confirmed"], "dimensions": evidence_counts["dimensions_confirmed"], "inventory": evidence_counts["inventory_confirmed"], "historical_guides": len(guide_amounts)},
            "derived": {"supplier_net_cost": cost_covered, "representative_catalog_cost": representative_cost, "historical_guide_median": historical_guide_median},
            "unknown": {"current_envia_quote_by_sku": item_count, "siigo_verified_cost": item_count, "commercial_policy_approval": item_count if not policies else 0},
        },
        "policy_hypothesis": ({
            "id": hypothesis.id, "name": hypothesis.name, "active": hypothesis.active,
            "approval_status": hypothesis.approval_status, "target_margin_percent": hypothesis.target_margin_percent,
            "minimum_margin_percent": hypothesis.minimum_margin_percent,
            "commission_percent": hypothesis.channel_commission_percent,
            "reserve_cap": hypothesis.logistics_reserve, "reserve_behavior": hypothesis.reserve_behavior,
            "max_shipping_subsidy": hypothesis.max_shipping_subsidy,
            "warning": "Valores editables para sensibilidad; no constituyen aprobación comercial.",
            "representative_inputs": {"catalog_cost": representative_cost, "historical_realized_guide_median": historical_guide_median},
            "sensitivity": sensitivity,
        } if hypothesis else None),
        "missing_action_rows": missing_rows,
        "externalWrites": 0,
    }
    warnings = [
        "La ausencia de peso, dimensiones, inventario o cotización Envía actual bloquea las cuatro modalidades; no equivale a costo cero.",
        "La política Barú incluida es una hipótesis editable no activada; sus porcentajes no están aprobados.",
        "El histórico real de guía sirve para sensibilidad, no reemplaza una cotización actual por destino y paquete.",
        "La utilidad realizada requiere costo real de guía, entrega, devoluciones y ajustes.",
    ]
    metrics = json.loads(json.dumps(metrics, default=str))
    run = None
    if persist:
        run = BulkSimulationRun.objects.create(
            assumptions=["Catálogo Barú con IVA incluido", "Lecturas locales persistidas", "Sin escrituras externas"],
            metrics=metrics,
            warnings=warnings,
            external_writes=0,
        )
    return {"run_id": str(run.id) if run else None, "basis": "LOCAL_READ_ONLY", "metrics": metrics, "warnings": warnings, "external_writes": 0}
