from rest_framework import serializers

from .models import (
    CatalogHistoryEvent,
    CanonicalCostSelection,
    ChannelSnapshot,
    CostObservation,
    InventorySourceSnapshot,
    InventoryLevel,
    IntegrationReadStatus,
    ExternalChannelProductSnapshot,
    MasterProduct,
    PricingPolicy,
    ProductImage,
    ProductVariant,
    ProviderConfig,
    SiigoProductSnapshot,
    ShopifyImportState,
    SkuReconciliation,
    SupplierCatalogImport,
    SodimacCatalogLink,
    SodimacCatalogObservation,
)
from .envia_readiness import serialize_variant_envia_readiness, serialize_variant_shipping_intelligence
from .commercial_costs import enrich_commercial_payload


class ProviderConfigSerializer(serializers.ModelSerializer):
    tax_treatment_label = serializers.CharField(source="get_tax_treatment_display", read_only=True)

    class Meta:
        model = ProviderConfig
        fields = "__all__"


class InventoryLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryLevel
        fields = [
            "location_external_id", "location_name", "available", "observed_at",
            "fulfills_online_orders", "location_active",
        ]


class IntegrationReadStatusSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = IntegrationReadStatus
        fields = ["system", "capability", "status", "status_label", "message", "evidence_reference", "record_count", "observed_at", "last_success_at", "external_writes", "details"]


class CostObservationSerializer(serializers.ModelSerializer):
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    provider_name = serializers.CharField(source="provider.name", read_only=True)

    class Meta:
        model = CostObservation
        fields = ["id", "source", "source_label", "provider_name", "raw_cost", "derived_net_cost", "currency", "tax_treatment", "tax_rate", "discount_percent", "observed_at", "valid_from", "valid_until", "evidence_reference"]


class CanonicalCostSerializer(serializers.ModelSerializer):
    source = serializers.CharField(source="observation.source", read_only=True)
    raw_cost = serializers.DecimalField(source="observation.raw_cost", max_digits=14, decimal_places=2, read_only=True)
    derived_net_cost = serializers.DecimalField(source="observation.derived_net_cost", max_digits=14, decimal_places=2, read_only=True)
    tax_treatment = serializers.CharField(source="observation.tax_treatment", read_only=True)
    tax_rate = serializers.DecimalField(source="observation.tax_rate", max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = CanonicalCostSelection
        fields = ["source", "raw_cost", "derived_net_cost", "tax_treatment", "tax_rate", "policy_name", "reason", "discrepancy", "selected_at"]


class InventorySourceSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_update_method_display", read_only=True)

    class Meta:
        model = InventorySourceSnapshot
        fields = ["id", "source_name", "warehouse_external_id", "warehouse_name", "reported_stock", "reserved_stock", "safety_stock", "available_to_promise", "stock_unknown", "observed_at", "freshness_minutes", "update_method", "method_label", "canonical", "evidence_reference"]


class SiigoProductSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiigoProductSnapshot
        fields = ["siigo_id", "sku", "name", "active", "sale_price", "tax_rate", "tax_included", "available_quantity", "warehouses", "match_status", "cost_status", "observed_at", "source_updated_at", "evidence_reference"]


class SodimacCatalogObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SodimacCatalogObservation
        fields = ["evidence_class", "observed_at", "expires_at", "publication_state", "inventory_available", "inventory_source", "field_comparison", "dimension_scores", "overall_score", "severity"]


class SodimacCatalogLinkSerializer(serializers.ModelSerializer):
    latest_observation = serializers.SerializerMethodField()

    class Meta:
        model = SodimacCatalogLink
        fields = ["id", "canonical_sku", "sodimac_sku", "listing_id", "listing_url", "status", "source_kind", "confidence", "evidence", "valid_from", "valid_until", "active", "manual_decision", "last_verified_at", "latest_observation"]

    def get_latest_observation(self, link):
        observation = link.observations.first()
        return SodimacCatalogObservationSerializer(observation).data if observation else None


class VariantSerializer(serializers.ModelSerializer):
    inventory_levels = InventoryLevelSerializer(many=True, read_only=True)
    inventory_sources = InventorySourceSerializer(many=True, read_only=True)
    cost_observations = CostObservationSerializer(many=True, read_only=True)
    canonical_cost = CanonicalCostSerializer(read_only=True)
    siigo_snapshots = SiigoProductSnapshotSerializer(many=True, read_only=True)
    reconciliation_status = serializers.SerializerMethodField()
    sodimac_catalog_links = SodimacCatalogLinkSerializer(many=True, read_only=True)
    envia_readiness = serializers.SerializerMethodField()
    shipping_intelligence = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ["id", "sku", "title", "barcode", "price", "compare_at_price", "provider_cost", "shopify_variant_id", "inventory_levels", "inventory_sources", "cost_observations", "canonical_cost", "siigo_snapshots", "sodimac_catalog_links", "reconciliation_status", "envia_readiness", "shipping_intelligence"]

    def get_reconciliation_status(self, variant):
        match = variant.supplier_matches.order_by("-id").first()
        return match.status if match else "MISSING"

    def get_envia_readiness(self, variant):
        return serialize_variant_envia_readiness(variant)

    def get_shipping_intelligence(self, variant):
        return serialize_variant_shipping_intelligence(
            variant,
            self.context.get("external_snapshots", {}),
            self.context.get("average_shipping_reference"),
        )


class ImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["source_url", "alt_text", "position"]


class ChannelSnapshotSerializer(serializers.ModelSerializer):
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    publication_quality = serializers.SerializerMethodField()
    payload = serializers.SerializerMethodField()

    class Meta:
        model = ChannelSnapshot
        fields = ["channel", "channel_label", "external_product_id", "external_variant_id", "state", "price", "compare_at_price", "cost", "inventory_available", "quality_score", "publication_quality", "payload", "observed_at"]

    def get_publication_quality(self, snapshot):
        external_id = str((snapshot.payload or {}).get("external_snapshot_id") or "")
        external = self.context.get("external_snapshots", {}).get(external_id)
        if snapshot.channel == "FALABELLA" and external:
            raw_score = (external.payload or {}).get("content_score")
            try:
                score = int(float(raw_score))
            except (TypeError, ValueError):
                score = None
            return {
                "score": score,
                "status": (external.payload or {}).get("qc_status") or "UNKNOWN",
                "basis": "CHANNEL_CONTENT_SCORE",
                "verified_channel_metric": score is not None,
            }
        if snapshot.channel == "SHOPIFY":
            return {"score": snapshot.quality_score, "status": None, "basis": "LOCAL_SHOPIFY_COMPLETENESS", "verified_channel_metric": False}
        return {"score": None, "status": None, "basis": "NOT_AVAILABLE", "verified_channel_metric": False}

    def get_payload(self, snapshot):
        return enrich_commercial_payload(
            snapshot.channel,
            snapshot.price,
            snapshot.payload,
            cost=snapshot.cost,
        )


class ExternalChannelProductSnapshotSerializer(serializers.ModelSerializer):
    matched_shopify_sku = serializers.CharField(source="matched_variant.sku", read_only=True)
    matched_shopify_product = serializers.CharField(source="matched_variant.product.title", read_only=True)
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj):
        if obj.image_url:
            return obj.image_url
        if obj.matched_variant_id:
            image = next(iter(obj.matched_variant.product.images.all()), None)
            return image.source_url if image else ""
        return ""

    class Meta:
        model = ExternalChannelProductSnapshot
        fields = [
            "id", "channel", "external_product_id", "external_variant_id", "sku", "barcode",
            "title", "brand", "category", "state", "price", "inventory_available", "currency",
            "url", "image_url", "match_status", "match_reason", "candidate_variant_ids",
            "matched_shopify_sku", "matched_shopify_product", "observed_at", "source_updated_at", "active",
            "payload",
        ]


class MasterProductSerializer(serializers.ModelSerializer):
    variants = VariantSerializer(many=True, read_only=True)
    images = ImageSerializer(many=True, read_only=True)
    channel_snapshots = ChannelSnapshotSerializer(many=True, read_only=True)

    class Meta:
        model = MasterProduct
        fields = ["id", "shopify_product_id", "title", "vendor", "brand", "category", "product_type", "status", "tags", "collections", "quality_score", "missing_fields", "needs_review", "variants", "images", "channel_snapshots", "updated_at"]


class PricingPolicySerializer(serializers.ModelSerializer):
    precedence_label = serializers.CharField(source="get_precedence_display", read_only=True)
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    provider_name = serializers.CharField(source="provider.name", read_only=True)
    approval_status_label = serializers.CharField(source="get_approval_status_display", read_only=True)
    reserve_behavior_label = serializers.CharField(source="get_reserve_behavior_display", read_only=True)

    class Meta:
        model = PricingPolicy
        fields = "__all__"


class ImportStateSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ShopifyImportState
        fields = "__all__"


class ReconciliationSerializer(serializers.ModelSerializer):
    supplier_sku = serializers.CharField(source="supplier_item.supplier_sku", read_only=True)
    supplier_description = serializers.CharField(source="supplier_item.description", read_only=True)
    supplier_price = serializers.DecimalField(source="supplier_item.supplier_price", max_digits=14, decimal_places=2, read_only=True)
    provider_name = serializers.CharField(source="supplier_item.provider.name", read_only=True)

    class Meta:
        model = SkuReconciliation
        fields = ["id", "supplier_sku", "supplier_description", "supplier_price", "provider_name", "variant", "status", "candidate_variant_ids", "reason", "reviewed"]


class HistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogHistoryEvent
        fields = "__all__"


class SupplierCatalogImportSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source="provider.name", read_only=True)

    class Meta:
        model = SupplierCatalogImport
        exclude = ["source_path_at_import"]
