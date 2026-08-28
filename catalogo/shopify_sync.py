"""Auditable Beta outbox for local price/inventory proposals to Shopify.

Scanning is local and safe. Execution is intentionally protected by four
independent gates and is never called by imports or signals directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.db import transaction
from django.utils import timezone

from config.constants import (
    EXTERNAL_WRITES_ENABLED,
    SHOPIFY_SYNC_MAX_BATCH,
    SHOPIFY_SYNC_SOURCE_MAX_AGE_MINUTES,
    SHOPIFY_SYNC_WRITES_ENABLED,
)

from .commercial_costs import enrich_commercial_payload
from .models import (
    CanonicalCostSelection,
    ChannelSnapshot,
    InventoryLevel,
    InventorySourceSnapshot,
    ProductVariant,
    ShopifySyncItem,
    ShopifySyncPolicy,
    ShopifySyncRun,
)


CONFIRMATION = "SHOPIFY_BETA_SYNC"


class ShopifySyncError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _money(value):
    if value in (None, ""):
        return None
    return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _number(value):
    if value is None:
        return None
    number = Decimal(str(value))
    if number != number.to_integral_value():
        raise ShopifySyncError("NON_INTEGER_INVENTORY", "Shopify requiere una cantidad entera para este piloto.")
    return int(number)


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_sync_policy():
    policy, _ = ShopifySyncPolicy.objects.get_or_create(
        key="PRIMARY",
        defaults={
            "environment": ShopifySyncPolicy.Environment.BETA,
            "scan_enabled": True,
            "writes_enabled": False,
            "price_enabled": True,
            "inventory_enabled": True,
            "maximum_batch_size": min(max(int(SHOPIFY_SYNC_MAX_BATCH), 1), 25),
            "source_max_age_minutes": max(int(SHOPIFY_SYNC_SOURCE_MAX_AGE_MINUTES), 1),
        },
    )
    return policy


def _shopify_snapshot(variant):
    return ChannelSnapshot.objects.filter(variant=variant, channel="SHOPIFY").order_by("-observed_at").first()


def _cost_for(variant, snapshot):
    selection = CanonicalCostSelection.objects.filter(variant=variant).select_related("observation").first()
    if selection and selection.observation.derived_net_cost is not None:
        return selection.observation.derived_net_cost, {
            "kind": "CANONICAL_COST",
            "reference": selection.observation.evidence_reference,
            "observed_at": selection.observation.observed_at.isoformat(),
        }
    if snapshot and snapshot.cost is not None:
        return snapshot.cost, {
            "kind": "SHOPIFY_UNIT_COST",
            "reference": "Shopify inventoryItem.unitCost; base tributaria pendiente de validación",
            "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
        }
    return None, {"kind": "MISSING"}


def _source_inventory(variant, policy):
    sources = list(
        InventorySourceSnapshot.objects.filter(
            variant=variant,
            canonical=True,
            stock_unknown=False,
            available_to_promise__isnull=False,
        ).exclude(source_name__icontains="shopify").order_by("-observed_at")[:2]
    )
    if not sources:
        return None, "INVENTORY_SOURCE_MISSING"
    if len(sources) > 1 and sources[0].observed_at == sources[1].observed_at:
        return None, "INVENTORY_SOURCE_AMBIGUOUS"
    source = sources[0]
    age = timezone.now() - source.observed_at
    if age.total_seconds() > policy.source_max_age_minutes * 60:
        return None, "INVENTORY_SOURCE_STALE"
    if source.available_to_promise < 0:
        return None, "INVENTORY_SOURCE_NEGATIVE"
    return source, None


def _target_inventory_level(variant, source):
    candidates = []
    source_names = {
        str(source.warehouse_name or "").strip().casefold(),
        str(source.source_name or "").strip().casefold(),
        str(source.provider.name if source.provider_id else "").strip().casefold(),
    } - {""}
    for level in InventoryLevel.objects.filter(variant=variant):
        if str(level.location_name or "").strip().casefold() in source_names:
            candidates.append(level)
    if len(candidates) != 1:
        return None
    return candidates[0]


def build_sync_proposal(variant, policy=None):
    policy = policy or get_sync_policy()
    sku = str(variant.sku or "").strip().upper()
    blockers = []
    fields = []
    previous = {}
    proposed = {}
    evidence = {}

    if not sku:
        blockers.append("SKU_MISSING")
    elif ProductVariant.objects.filter(sku__iexact=sku).count() != 1:
        blockers.append("SKU_NOT_LITERAL_UNIQUE")
    if not variant.shopify_variant_id or not variant.product.shopify_product_id:
        blockers.append("SHOPIFY_IDS_MISSING")

    snapshot = _shopify_snapshot(variant)
    if not snapshot:
        blockers.append("SHOPIFY_SNAPSHOT_MISSING")
    else:
        payload = snapshot.payload or {}
        evidence["shopify_snapshot"] = {
            "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
            "state": snapshot.state,
        }
        previous["price"] = str(_money(snapshot.price)) if snapshot.price is not None else None
        previous["compare_at_price"] = str(_money(snapshot.compare_at_price)) if snapshot.compare_at_price is not None else None

        if policy.price_enabled:
            cost, cost_evidence = _cost_for(variant, snapshot)
            evidence["price_cost"] = cost_evidence
            commercial = enrich_commercial_payload("SHOPIFY", snapshot.price, snapshot.payload, cost=cost)
            simulation = (commercial.get("profitability") or {}).get("pricing_simulation") or {}
            desired_price = _money(simulation.get("suggested_price"))
            if desired_price is None:
                blockers.append("PRICE_NOT_CALCULABLE")
            elif desired_price <= 0:
                blockers.append("PRICE_INVALID")
            elif _money(snapshot.price) != desired_price:
                proposed["price"] = str(desired_price)
                fields.append("PRICE")
                evidence["price_rule"] = {
                    "basis": simulation.get("formula_basis"),
                    "status": simulation.get("status"),
                    "target_net_margin_percent": simulation.get("target_net_margin_percent"),
                    "logistics_reserve_percent": simulation.get("logistics_reserve_percent"),
                    "logistics_reserve_cap": simulation.get("logistics_reserve_cap"),
                }

        if policy.inventory_enabled:
            source, source_error = _source_inventory(variant, policy)
            if source_error:
                blockers.append(source_error)
            else:
                target = _target_inventory_level(variant, source)
                inventory_item_id = str((snapshot.payload or {}).get("inventoryItemId") or "")
                if not inventory_item_id:
                    blockers.append("SHOPIFY_INVENTORY_ITEM_ID_MISSING")
                elif not target:
                    blockers.append("SHOPIFY_LOCATION_NOT_UNIQUE")
                else:
                    desired_quantity = _number(source.available_to_promise)
                    current_quantity = _number(target.available) if target.available is not None else None
                    previous["inventory"] = current_quantity
                    proposed["inventory_item_id"] = inventory_item_id
                    proposed["location_id"] = target.location_external_id
                    proposed["location_name"] = target.location_name
                    evidence["inventory_source"] = {
                        "source_name": source.source_name,
                        "warehouse_name": source.warehouse_name,
                        "observed_at": source.observed_at.isoformat(),
                        "evidence_reference": source.evidence_reference,
                    }
                    if desired_quantity != current_quantity:
                        proposed["inventory"] = desired_quantity
                        fields.append("INVENTORY")

    status = ShopifySyncItem.Status.READY if fields else (
        ShopifySyncItem.Status.BLOCKED if blockers else ShopifySyncItem.Status.NO_CHANGE
    )
    identity = {
        "variant_id": str(variant.id),
        "shopify_variant_id": variant.shopify_variant_id,
        "shopify_product_id": variant.product.shopify_product_id,
        "sku": sku,
        "fields": fields,
        "previous": previous,
        "proposed": proposed,
        "evidence": evidence,
        "blockers": blockers,
    }
    return {
        **identity,
        "status": status,
        "fingerprint": _fingerprint(identity),
        "rollback": previous,
    }


@transaction.atomic
def scan_shopify_sync(*, skus=None, trigger="MANUAL_PREVIEW", limit=None):
    policy = get_sync_policy()
    requested = sorted({str(sku).strip().upper() for sku in (skus or []) if str(sku).strip()})
    run = ShopifySyncRun.objects.create(
        mode=ShopifySyncRun.Mode.PREVIEW if requested else ShopifySyncRun.Mode.SCAN,
        trigger=trigger,
        requested_skus=requested,
    )
    queryset = ProductVariant.objects.select_related("product").exclude(shopify_variant_id="").exclude(product__shopify_product_id="")
    if requested:
        queryset = queryset.filter(sku__in=requested)
    maximum = max(1, min(int(limit or queryset.count() or 1), 10000))
    counts = {"scanned": 0, "ready": 0, "blocked": 0, "no_change": 0}
    for variant in queryset.order_by("sku", "id")[:maximum]:
        proposal = build_sync_proposal(variant, policy)
        ShopifySyncItem.objects.create(
            run=run,
            variant=variant,
            sku=proposal["sku"],
            status=proposal["status"],
            fields=proposal["fields"],
            previous_values=proposal["previous"],
            proposed_values=proposal["proposed"],
            source_evidence=proposal["evidence"],
            blockers=proposal["blockers"],
            fingerprint=proposal["fingerprint"],
            rollback_payload=proposal["rollback"],
        )
        counts["scanned"] += 1
        if proposal["status"] == ShopifySyncItem.Status.READY:
            counts["ready"] += 1
        elif proposal["status"] == ShopifySyncItem.Status.BLOCKED:
            counts["blocked"] += 1
        else:
            counts["no_change"] += 1
    run.scanned_count = counts["scanned"]
    run.ready_count = counts["ready"]
    run.blocked_count = counts["blocked"]
    run.no_change_count = counts["no_change"]
    run.status = ShopifySyncRun.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()
    return run


class ShopifyGraphQLClient:
    def __init__(self):
        domain = str(os.environ.get("SHOPIFY_STORE_DOMAIN") or "").strip().lower()
        self.domain = domain.removeprefix("https://").removeprefix("http://").split("/")[0]
        self.client_id = str(os.environ.get("SHOPIFY_CLIENT_ID") or "").strip()
        self.client_secret = str(os.environ.get("SHOPIFY_CLIENT_SECRET") or "").strip()
        self.api_version = str(os.environ.get("SHOPIFY_API_VERSION") or "2026-07").strip()
        if not all([self.domain, self.client_id, self.client_secret]):
            raise ShopifySyncError("SHOPIFY_CREDENTIALS_MISSING", "Faltan referencias seguras de Shopify en el entorno Beta.")
        auth = requests.post(
            f"https://{self.domain}/admin/oauth/access_token",
            json={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret},
            timeout=30,
        )
        body = auth.json() if auth.content else {}
        if not auth.ok or not body.get("access_token"):
            raise ShopifySyncError("SHOPIFY_AUTH_REJECTED", f"Shopify rechazó la autenticación con HTTP {auth.status_code}.")
        self.token = body["access_token"]
        self.endpoint = f"https://{self.domain}/admin/api/{self.api_version}/graphql.json"

    def call(self, query, variables):
        last_error = None
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    self.endpoint,
                    headers={"content-type": "application/json", "x-shopify-access-token": self.token},
                    json={"query": query, "variables": variables},
                    timeout=60,
                )
            except requests.RequestException as error:
                last_error = ShopifySyncError("SHOPIFY_NETWORK_ERROR", error.__class__.__name__)
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise last_error from error
            if response.status_code == 429 or response.status_code >= 500:
                last_error = ShopifySyncError("SHOPIFY_RETRYABLE_HTTP_ERROR", f"HTTP {response.status_code}")
                if attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = min(max(float(retry_after), 0), 10) if retry_after else 2 ** (attempt - 1)
                    except (TypeError, ValueError):
                        delay = 2 ** (attempt - 1)
                    time.sleep(delay)
                    continue
                raise last_error
            if not response.ok:
                raise ShopifySyncError("SHOPIFY_HTTP_ERROR", f"HTTP {response.status_code}")
            try:
                body = response.json()
            except requests.JSONDecodeError as error:
                raise ShopifySyncError("SHOPIFY_INVALID_JSON", "Shopify devolvió una respuesta no JSON.") from error
            if body.get("errors"):
                codes = sorted({str((error.get("extensions") or {}).get("code") or "GRAPHQL_ERROR") for error in body["errors"]})
                raise ShopifySyncError("SHOPIFY_GRAPHQL_ERROR", ", ".join(codes))
            return body.get("data") or {}
        raise last_error or ShopifySyncError("SHOPIFY_UNKNOWN_ERROR", "La operación no pudo completarse.")

    def update_price(self, item):
        query = """
        mutation PamoVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants, allowPartialUpdates: false) {
            productVariants { id price compareAtPrice }
            userErrors { field message }
          }
        }
        """
        data = self.call(query, {
            "productId": item.variant.product.shopify_product_id,
            "variants": [{"id": item.variant.shopify_variant_id, "price": item.proposed_values["price"]}],
        })
        payload = data.get("productVariantsBulkUpdate") or {}
        if payload.get("userErrors"):
            raise ShopifySyncError("SHOPIFY_PRICE_USER_ERROR", "; ".join(error.get("message", "Error") for error in payload["userErrors"]))
        return (payload.get("productVariants") or [{}])[0]

    def set_inventory(self, item):
        query = """
        mutation PamoInventorySet($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
          inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
            inventoryAdjustmentGroup { reason referenceDocumentUri changes { name delta quantityAfterChange } }
            userErrors { code field message }
          }
        }
        """
        data = self.call(query, {
            "idempotencyKey": str(item.idempotency_key),
            "input": {
                "name": "available",
                "reason": "correction",
                "referenceDocumentUri": f"pamo://shopify-sync/{item.id}",
                "quantities": [{
                    "inventoryItemId": item.proposed_values["inventory_item_id"],
                    "locationId": item.proposed_values["location_id"],
                    "quantity": item.proposed_values["inventory"],
                    "compareQuantity": item.previous_values.get("inventory"),
                }],
            },
        })
        payload = data.get("inventorySetQuantities") or {}
        if payload.get("userErrors"):
            codes = {str(error.get("code") or "") for error in payload["userErrors"]}
            code = "SHOPIFY_INVENTORY_CONFLICT" if "COMPARE_QUANTITY_STALE" in codes else "SHOPIFY_INVENTORY_USER_ERROR"
            raise ShopifySyncError(code, "; ".join(error.get("message", "Error") for error in payload["userErrors"]))
        return payload.get("inventoryAdjustmentGroup") or {}

    def reread(self, variant_id):
        query = """
        query PamoSyncVerify($id: ID!) {
          productVariant(id: $id) {
            id price compareAtPrice
            inventoryItem { id inventoryLevels(first: 100) { nodes { location { id name } quantities(names: [\"available\"]) { name quantity } } } }
          }
        }
        """
        return (self.call(query, {"id": variant_id}).get("productVariant") or {})


def _verify_shopify_result(item, verified):
    if not verified or str(verified.get("id") or "") != str(item.variant.shopify_variant_id):
        raise ShopifySyncError("SHOPIFY_REREAD_MISSING", "La variante no apareció en la relectura posterior.")
    if "PRICE" in item.fields:
        expected_price = _money(item.proposed_values.get("price"))
        actual_price = _money(verified.get("price"))
        if actual_price != expected_price:
            raise ShopifySyncError(
                "SHOPIFY_PRICE_VERIFY_MISMATCH",
                f"La relectura no confirmó el precio esperado ({expected_price}).",
            )
    if "INVENTORY" in item.fields:
        expected_location = str(item.proposed_values.get("location_id") or "")
        expected_quantity = _number(item.proposed_values.get("inventory"))
        levels = (((verified.get("inventoryItem") or {}).get("inventoryLevels") or {}).get("nodes") or [])
        matching = [level for level in levels if str((level.get("location") or {}).get("id") or "") == expected_location]
        if len(matching) != 1:
            raise ShopifySyncError("SHOPIFY_INVENTORY_VERIFY_LOCATION", "La ubicación exacta no apareció en la relectura.")
        quantities = matching[0].get("quantities") or []
        available = next((value.get("quantity") for value in quantities if value.get("name") == "available"), None)
        if _number(available) != expected_quantity:
            raise ShopifySyncError(
                "SHOPIFY_INVENTORY_VERIFY_MISMATCH",
                f"La relectura no confirmó el inventario esperado ({expected_quantity}).",
            )


def execute_shopify_pilot(*, run_id, skus, confirmation):
    policy = get_sync_policy()
    requested = sorted({str(sku).strip().upper() for sku in skus if str(sku).strip()})
    if confirmation != CONFIRMATION:
        raise ShopifySyncError("CONFIRMATION_REQUIRED", "La confirmación literal del piloto no coincide.")
    if policy.environment != ShopifySyncPolicy.Environment.BETA:
        raise ShopifySyncError("BETA_ONLY", "Este escritor solo puede operar en Beta.")
    if not (EXTERNAL_WRITES_ENABLED and SHOPIFY_SYNC_WRITES_ENABLED and policy.writes_enabled):
        raise ShopifySyncError("WRITE_GATES_DISABLED", "Las tres compuertas de escritura permanecen apagadas.")
    maximum = min(policy.maximum_batch_size, max(int(SHOPIFY_SYNC_MAX_BATCH), 1), 25)
    if not requested or len(requested) > maximum:
        raise ShopifySyncError("ALLOWLIST_REQUIRED", f"El piloto requiere entre 1 y {maximum} SKU exactos.")
    if sorted({str(sku).strip().upper() for sku in policy.allowlisted_skus}) != requested:
        raise ShopifySyncError("POLICY_ALLOWLIST_MISMATCH", "La lista aprobada en la política no coincide con el piloto.")

    source_run = ShopifySyncRun.objects.get(pk=run_id)
    items = list(source_run.items.filter(sku__in=requested, status=ShopifySyncItem.Status.READY).select_related("variant__product"))
    if len(items) != len(requested):
        raise ShopifySyncError("READY_ITEMS_MISMATCH", "Todos los SKU deben estar listos en la misma vista previa.")
    execution = ShopifySyncRun.objects.create(mode=ShopifySyncRun.Mode.EXECUTE, trigger="AUTHORIZED_BETA_PILOT", requested_skus=requested)
    client = ShopifyGraphQLClient()
    for source in items:
        proposal = build_sync_proposal(source.variant, policy)
        if proposal["fingerprint"] != source.fingerprint:
            ShopifySyncItem.objects.create(
                run=execution, variant=source.variant, sku=source.sku, status=ShopifySyncItem.Status.CONFLICT,
                fields=source.fields, previous_values=source.previous_values, proposed_values=source.proposed_values,
                source_evidence=source.source_evidence, blockers=["PREVIEW_STALE"], fingerprint=proposal["fingerprint"],
                rollback_payload=source.rollback_payload, last_error_code="PREVIEW_STALE",
            )
            execution.failed_count += 1
            continue
        item = ShopifySyncItem.objects.create(
            run=execution, variant=source.variant, sku=source.sku, status=ShopifySyncItem.Status.READY,
            fields=source.fields, previous_values=source.previous_values, proposed_values=source.proposed_values,
            source_evidence=source.source_evidence, blockers=source.blockers, fingerprint=source.fingerprint,
            rollback_payload=source.rollback_payload,
        )
        try:
            result = {}
            if "INVENTORY" in item.fields:
                result["inventory"] = client.set_inventory(item)
                item.external_writes += 1
            if "PRICE" in item.fields:
                result["price"] = client.update_price(item)
                item.external_writes += 1
            verified = client.reread(item.variant.shopify_variant_id)
            _verify_shopify_result(item, verified)
            result["verified"] = verified
            item.source_evidence = {**item.source_evidence, "shopify_result": result}
            item.status = ShopifySyncItem.Status.SUCCEEDED
            execution.succeeded_count += 1
        except ShopifySyncError as error:
            item.status = ShopifySyncItem.Status.CONFLICT if error.code == "SHOPIFY_INVENTORY_CONFLICT" else ShopifySyncItem.Status.FAILED
            item.last_error_code = error.code
            item.last_error_message = str(error)[:300]
            execution.failed_count += 1
        item.attempts += 1
        item.save()
        execution.external_writes += item.external_writes
    execution.scanned_count = len(items)
    execution.status = ShopifySyncRun.Status.SUCCEEDED if execution.failed_count == 0 else ShopifySyncRun.Status.PARTIAL
    execution.finished_at = timezone.now()
    execution.save()
    return execution


def serialize_sync_run(run, *, item_limit=100):
    items = run.items.select_related("variant__product").all()[:item_limit] if run else []
    return {
        "id": str(run.id) if run else None,
        "mode": run.mode if run else None,
        "status": run.status if run else "NOT_RUN",
        "trigger": run.trigger if run else None,
        "counts": {
            "scanned": run.scanned_count if run else 0,
            "ready": run.ready_count if run else 0,
            "blocked": run.blocked_count if run else 0,
            "no_change": run.no_change_count if run else 0,
            "succeeded": run.succeeded_count if run else 0,
            "failed": run.failed_count if run else 0,
        },
        "external_writes": run.external_writes if run else 0,
        "started_at": run.started_at if run else None,
        "finished_at": run.finished_at if run else None,
        "items": [{
            "id": str(item.id), "sku": item.sku, "title": item.variant.product.title,
            "status": item.status, "fields": item.fields, "previous": item.previous_values,
            "proposed": item.proposed_values, "blockers": item.blockers,
            "evidence": item.source_evidence, "external_writes": item.external_writes,
        } for item in items],
    }


def sync_workspace():
    policy = get_sync_policy()
    latest = ShopifySyncRun.objects.first()
    return {
        "environment": policy.environment,
        "policy": {
            "scan_enabled": policy.scan_enabled,
            "writes_enabled": policy.writes_enabled,
            "price_enabled": policy.price_enabled,
            "inventory_enabled": policy.inventory_enabled,
            "maximum_batch_size": policy.maximum_batch_size,
            "debounce_seconds": policy.debounce_seconds,
            "source_max_age_minutes": policy.source_max_age_minutes,
            "allowlisted_skus": policy.allowlisted_skus,
        },
        "gates": {
            "global_external_writes": bool(EXTERNAL_WRITES_ENABLED),
            "shopify_sync_writes": bool(SHOPIFY_SYNC_WRITES_ENABLED),
            "policy_writes": policy.writes_enabled,
            "execution_allowed": bool(EXTERNAL_WRITES_ENABLED and SHOPIFY_SYNC_WRITES_ENABLED and policy.writes_enabled and policy.environment == "BETA"),
        },
        "scheduler": {
            "contract": "Ejecutar `manage.py run_shopify_sync_cycle` de forma recurrente en el worker Beta.",
            "deployed": False,
            "automatic_external_execution": False,
        },
        "latest_run": serialize_sync_run(latest),
    }
