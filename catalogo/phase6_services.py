"""Puente entre los motores puros de Fase 6 y la persistencia SQLite local."""

from __future__ import annotations

from datetime import timezone as dt_timezone
from datetime import date
from decimal import Decimal
import json

from django.db import transaction
from django.utils import timezone

from .models import (
    CatalogHistoryEvent,
    MultwarehouseSimulationRun,
    PhysicalEvidenceCandidate,
    PricingLocalBatch,
    PricingPolicy,
    ProductVariant,
    ProviderConfig,
)
from .phase6 import (
    PRICING_MODES,
    ROUNDING_CHOICES,
    demo_case,
    preview_pricing,
    rule_match,
    simulate_multwarehouse_cart,
    stable_fingerprint,
)


class Phase6InputError(ValueError):
    pass


def _safe(payload):
    return json.loads(json.dumps(payload, default=str))


def _decimal(value, default=None):
    if value in (None, ""):
        return default
    return Decimal(str(value))


# Conserva compatibilidad con reglas anteriores y amplía su combinación JSON sin migrarlas destructivamente.
def serialize_rule(policy):
    combination = dict(policy.combination or {})
    filters = dict(combination.get("filters") or {})
    legacy_filters = {
        "sku": policy.sku, "channel": policy.channel, "provider": policy.provider.name if policy.provider else "",
        "brand": policy.brand, "collection": policy.collection, "category": policy.category,
        "product_type": policy.product_type,
    }
    for key, value in legacy_filters.items():
        if value and not filters.get(key):
            filters[key] = [value]
    pricing = {
        "mode": "GROSS_MARGIN",
        "value": str(policy.target_margin_percent),
        "channel_commission_percent": str(policy.channel_commission_percent),
        "channel_fixed_charge": str(policy.channel_fixed_charge),
        "payment_percent": "0",
        "payment_fixed_charge": "0",
        "administrative_percent": "0",
        "administrative_fixed_charge": "0",
        "logistics_percent": "0",
        "logistics_fixed_charge": "0",
        "additional_costs": [],
        "minimum_margin_percent": str(policy.minimum_margin_percent or policy.target_margin_percent),
        "rounding_increment": policy.rounding_increment,
        "reserve_cap": str(policy.logistics_reserve),
        "maximum_shipping_subsidy": str(policy.max_shipping_subsidy),
        "minimum_price": None,
        "maximum_price": None,
    }
    pricing.update(combination.get("pricing") or {})
    return {
        "id": str(policy.id), "name": policy.name, "active": policy.active,
        "priority": policy.priority, "valid_from": policy.valid_from, "valid_until": policy.valid_until,
        "filters": filters, "pricing": pricing, "exception": combination.get("exception") or {},
        "responsible": combination.get("responsible") or "", "notes": combination.get("notes") or policy.explanation,
        "source": combination.get("source") or {},
        "approval_status": policy.approval_status, "simulation_only": policy.simulation_only,
    }


def save_rule(payload):
    name = str(payload.get("name") or "").strip()
    pricing = dict(payload.get("pricing") or {})
    mode = str(pricing.get("mode") or "").upper()
    if not name:
        raise Phase6InputError("La regla necesita nombre.")
    if mode not in PRICING_MODES:
        raise Phase6InputError("Elige markup, incremento fijo o margen bruto.")
    if _decimal(pricing.get("value")) is None:
        raise Phase6InputError("La regla necesita un valor de cálculo.")
    rounding = int(pricing.get("rounding_increment") or 100)
    if rounding not in ROUNDING_CHOICES:
        raise Phase6InputError("El redondeo permitido es 100, 500 o 1.000 COP.")
    valid_from = date.fromisoformat(payload["valid_from"]) if payload.get("valid_from") else None
    valid_until = date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None
    if valid_from and valid_until and valid_from > valid_until:
        raise Phase6InputError("La vigencia final no puede ser anterior a la inicial.")
    minimum_price, maximum_price = _decimal(pricing.get("minimum_price")), _decimal(pricing.get("maximum_price"))
    if minimum_price is not None and maximum_price is not None and minimum_price > maximum_price:
        raise Phase6InputError("El precio mínimo no puede superar el máximo.")
    filters = dict(payload.get("filters") or {})
    provider_name = (filters.get("provider") or [None])[0] if isinstance(filters.get("provider"), list) else filters.get("provider")
    provider = ProviderConfig.objects.filter(name__iexact=provider_name).first() if provider_name else None
    core_value = _decimal(pricing.get("value"), Decimal("0")) if mode == "GROSS_MARGIN" else _decimal(pricing.get("minimum_margin_percent"), Decimal("0"))
    defaults = {
        "name": name, "active": bool(payload.get("active", False)), "priority": int(payload.get("priority") or 0),
        "channel": (filters.get("channel") or [""])[0] if isinstance(filters.get("channel"), list) else filters.get("channel", ""),
        "provider": provider, "collection": "", "brand": "", "category": "", "product_type": "", "sku": "",
        "target_margin_percent": core_value, "channel_commission_percent": _decimal(pricing.get("channel_commission_percent"), Decimal("0")),
        "channel_fixed_charge": _decimal(pricing.get("channel_fixed_charge"), Decimal("0")),
        "logistics_reserve": _decimal(pricing.get("reserve_cap"), Decimal("0")), "rounding_increment": rounding,
        "max_shipping_subsidy": _decimal(pricing.get("maximum_shipping_subsidy"), Decimal("0")),
        "minimum_margin_percent": _decimal(pricing.get("minimum_margin_percent"), Decimal("0")),
        "simulation_only": True, "approval_status": PricingPolicy.ApprovalStatus.HYPOTHESIS,
        "reserve_behavior": PricingPolicy.ReserveBehavior.CAP, "valid_from": valid_from,
        "valid_until": valid_until, "explanation": str(payload.get("notes") or "")[:4000],
        "combination": {"filters": filters, "pricing": pricing, "exception": payload.get("exception") or {}, "responsible": payload.get("responsible") or "", "notes": payload.get("notes") or "", "source": _safe(payload.get("source") or {})},
    }
    policy_id = payload.get("id")
    if policy_id:
        policy = PricingPolicy.objects.get(pk=policy_id)
        for field, value in defaults.items():
            setattr(policy, field, value)
        policy.save()
    else:
        policy = PricingPolicy.objects.create(**defaults)
    return serialize_rule(policy)


def _variant_context(variant, shipping_subsidy_used=None):
    product = variant.product
    canonical = getattr(variant, "canonical_cost", None)
    observation = canonical.observation if canonical else None
    provider = observation.provider if observation and observation.provider else ProviderConfig.objects.filter(name__iexact=product.vendor).first()
    raw_cost = None
    if observation:
        raw_cost = observation.raw_cost if observation.raw_cost is not None else observation.derived_net_cost
    if raw_cost is None:
        raw_cost = variant.provider_cost
    inventory_rows = list(variant.inventory_sources.all())
    known_inventory = [row.available_to_promise for row in inventory_rows if not row.stock_unknown and row.available_to_promise is not None]
    return {
        "variant_id": str(variant.id), "sku": variant.sku, "title": product.title,
        "before_price": variant.price, "canonical_cost": raw_cost,
        "cost_source": (f"{observation.source}: {observation.evidence_reference}" if observation else "ProductVariant.provider_cost" if raw_cost is not None else "UNKNOWN"),
        "tax_treatment": (observation.tax_treatment if observation else provider.tax_treatment if provider else "PENDING"),
        "tax_rate": (observation.tax_rate if observation and observation.tax_rate is not None else provider.tax_rate if provider else None),
        "provider": [provider.name if provider else product.vendor], "brand": [product.brand],
        "collection": list(product.collections or []), "category": [product.category], "product_type": [product.product_type],
        "tags": list(product.tags or []), "channel": sorted({row.channel for row in product.channel_snapshots.all()}),
        "warehouse": sorted({row.warehouse_name for row in inventory_rows if row.warehouse_name}),
        "inventory": sum(known_inventory) if known_inventory else None,
        "shipping_subsidy_used": shipping_subsidy_used,
    }


def select_contexts(selection):
    queryset = ProductVariant.objects.select_related(
        "product", "canonical_cost__observation__provider",
    ).prefetch_related("inventory_sources", "product__channel_snapshots").order_by("sku", "id")
    skus = [str(value).strip() for value in selection.get("sku") or [] if str(value).strip()]
    if skus:
        queryset = queryset.filter(sku__in=skus)
    limit = min(max(int(selection.get("limit") or 50), 1), 5000)
    contexts = []
    filter_rule = {"active": True, "filters": {key: value for key, value in selection.items() if key not in {"limit", "shipping_subsidy_used"}}}
    for variant in queryset[:5000]:
        context = _variant_context(variant, shipping_subsidy_used=selection.get("shipping_subsidy_used"))
        matches, _ = rule_match(filter_rule, context, on_date=timezone.localdate())
        if matches:
            contexts.append(context)
        if len(contexts) >= limit:
            break
    return contexts


def create_pricing_preview(selection, draft_rule=None, actor_label="local-operator", include_saved=True):
    contexts = select_contexts(selection)
    rules = [serialize_rule(policy) for policy in PricingPolicy.objects.all()] if include_saved else []
    if draft_rule:
        draft = dict(draft_rule)
        draft.setdefault("id", "DRAFT-IN-CURRENT-BROWSER")
        draft.setdefault("name", "Borrador actual")
        draft["saved_active_state"] = bool(draft.get("active", False))
        draft["active"] = True
        rules.append(draft)
    preview = preview_pricing(contexts, rules, on_date=timezone.localdate())
    signature = {
        "variant_ids": [row["variant_id"] for row in contexts], "selection": selection,
        "rules": rules, "engine": "PHASE6_V1",
    }
    fingerprint = stable_fingerprint(signature)
    batch, _ = PricingLocalBatch.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "status": PricingLocalBatch.Status.PREVIEW if preview["status"] == "READY_LOCAL" else PricingLocalBatch.Status.BLOCKED,
            "selection_snapshot": _safe(selection), "rule_snapshot": _safe(rules),
            "preview_payload": _safe(preview), "actor_label": actor_label[:160], "external_writes": 0,
        },
    )
    return batch, preview


# La aplicación es transaccional, idempotente y falla cerrada si cambió el precio desde la vista previa.
@transaction.atomic
def apply_pricing_batch(batch_id, actor_label="local-operator"):
    batch = PricingLocalBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == PricingLocalBatch.Status.APPLIED_LOCAL:
        return batch
    if batch.status != PricingLocalBatch.Status.PREVIEW:
        raise Phase6InputError("El lote no está listo; resuelve bloqueos y conflictos.")
    rows = batch.preview_payload.get("rows") or []
    if any(row.get("blockers") or row.get("final_price") in (None, "") for row in rows):
        raise Phase6InputError("UNKNOWN o conflicto bloquea la aplicación completa del lote.")
    rollback, applied = {}, {}
    for row in rows:
        variant = ProductVariant.objects.select_for_update().get(pk=row["variant_id"])
        before = str(variant.price) if variant.price is not None else None
        preview_before = str(row.get("before_price")) if row.get("before_price") is not None else None
        if before != preview_before:
            raise Phase6InputError(f"El precio de {variant.sku} cambió después de la vista previa.")
        after = Decimal(str(row["final_price"]))
        rollback[str(variant.id)] = {"sku": variant.sku, "price": before}
        variant.price = after
        variant.save(update_fields=["price"])
        applied[str(variant.id)] = {"sku": variant.sku, "price": str(after)}
        CatalogHistoryEvent.objects.create(
            entity_type="ProductVariant", entity_id=str(variant.id), action="PHASE6_PRICE_APPLIED_LOCAL",
            before={"price": before}, after={"price": str(after), "batch_id": str(batch.id)},
            actor_label=actor_label[:160], reversible=True,
        )
    batch.rollback_payload = rollback
    batch.applied_payload = applied
    batch.status = PricingLocalBatch.Status.APPLIED_LOCAL
    batch.actor_label = actor_label[:160]
    batch.save(update_fields=["rollback_payload", "applied_payload", "status", "actor_label", "updated_at"])
    return batch


@transaction.atomic
def reverse_pricing_batch(batch_id, actor_label="local-operator"):
    batch = PricingLocalBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == PricingLocalBatch.Status.REVERSED:
        return batch
    if batch.status != PricingLocalBatch.Status.APPLIED_LOCAL:
        raise Phase6InputError("Solo un lote aplicado localmente puede revertirse.")
    for variant_id, before in batch.rollback_payload.items():
        variant = ProductVariant.objects.select_for_update().get(pk=variant_id)
        applied = batch.applied_payload.get(variant_id, {}).get("price")
        current = str(variant.price) if variant.price is not None else None
        if current != applied:
            raise Phase6InputError(f"{variant.sku} cambió después del lote; reversión detenida.")
        previous = before.get("price")
        variant.price = Decimal(previous) if previous is not None else None
        variant.save(update_fields=["price"])
        CatalogHistoryEvent.objects.create(
            entity_type="ProductVariant", entity_id=str(variant.id), action="PHASE6_PRICE_REVERSED_LOCAL",
            before={"price": current, "batch_id": str(batch.id)}, after={"price": previous},
            actor_label=actor_label[:160], reversible=False,
        )
    batch.status = PricingLocalBatch.Status.REVERSED
    batch.actor_label = actor_label[:160]
    batch.save(update_fields=["status", "actor_label", "updated_at"])
    return batch


def serialize_batch(batch, include_preview=False):
    result = {
        "id": str(batch.id), "fingerprint": batch.fingerprint, "status": batch.status,
        "actor_label": batch.actor_label, "created_at": batch.created_at, "updated_at": batch.updated_at,
        "summary": (batch.preview_payload or {}).get("summary") or {}, "reversible": batch.status == PricingLocalBatch.Status.APPLIED_LOCAL,
        "external_writes": batch.external_writes,
    }
    if include_preview:
        result["preview"] = batch.preview_payload
    return result


def _package_ready(variant):
    now = timezone.now()
    fields = set()
    candidates = PhysicalEvidenceCandidate.objects.filter(
        variant=variant, scope="PACKAGE", classification="CONFIRMED", conflict=False,
        decisions__action="APPROVE_LOCAL",
    ).prefetch_related("decisions")
    for candidate in candidates:
        latest = candidate.decisions.first()
        if latest and (latest.expires_at is None or latest.expires_at >= now):
            fields.add(candidate.field)
    return fields == {"WEIGHT", "LENGTH", "WIDTH", "HEIGHT"}


def _cart_lines_from_db(cart):
    lines = []
    for raw in cart:
        sku = str(raw.get("sku") or "").strip()
        variants = list(ProductVariant.objects.filter(sku=sku).select_related("canonical_cost__observation", "product").prefetch_related("inventory_sources")[:2])
        if len(variants) != 1:
            lines.append({"sku": sku, "quantity": raw.get("quantity"), "unit_price": None, "unit_cost": None, "package_ready": False, "inventory": []})
            continue
        variant = variants[0]
        observation = getattr(getattr(variant, "canonical_cost", None), "observation", None)
        cost = (observation.raw_cost if observation and observation.raw_cost is not None else variant.provider_cost)
        inventory = [{
            "origin": row.warehouse_name or row.source_name, "available": row.available_to_promise,
            "unknown": row.stock_unknown, "observed_at": row.observed_at.astimezone(dt_timezone.utc).isoformat(),
            "freshness_minutes": row.freshness_minutes,
        } for row in variant.inventory_sources.all()]
        lines.append({
            "sku": sku, "quantity": raw.get("quantity"), "unit_price": variant.price,
            "unit_cost": cost, "package_ready": _package_ready(variant), "inventory": inventory,
        })
    return lines


def run_multwarehouse(payload, actor_label="local-operator"):
    if payload.get("demo_case"):
        result = demo_case(str(payload["demo_case"]).upper())
    else:
        lines = _cart_lines_from_db(payload.get("cart") or [])
        result = simulate_multwarehouse_cart(
            lines, demo_fixture=False, customer_shipping_charge=payload.get("customer_shipping_charge", 0),
            minimum_margin_percent=payload.get("minimum_margin_percent", 0),
        )
    fingerprint = stable_fingerprint({"payload": payload, "engine": "PHASE6_V1"})
    run, _ = MultwarehouseSimulationRun.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "input_snapshot": _safe(payload), "result_snapshot": _safe(result), "status": result["status"],
            "quote_basis": result["quote_basis"], "actor_label": actor_label[:160], "external_writes": 0,
        },
    )
    return run, result
