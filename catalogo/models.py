import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


PERCENT_VALIDATORS = [MinValueValidator(0), MaxValueValidator(100)]


class Channel(models.TextChoices):
    SHOPIFY = "SHOPIFY", "Shopify"
    MERCADO_LIBRE = "MERCADO_LIBRE", "Mercado Libre"
    FALABELLA = "FALABELLA", "Falabella"
    SODIMAC = "SODIMAC", "Sodimac"
    MADECENTRO = "MADECENTRO", "Madecentro"
    RAPPI = "RAPPI", "Rappi"


class ProviderConfig(models.Model):
    class TaxTreatment(models.TextChoices):
        INCLUDED = "INCLUDED", "IVA incluido"
        EXCLUDED = "EXCLUDED", "IVA no incluido"
        PENDING = "PENDING", "IVA pendiente de confirmar"

    name = models.CharField(max_length=160, unique=True)
    source_reference = models.CharField(max_length=300, blank=True)
    currency = models.CharField(max_length=3, default="COP")
    tax_treatment = models.CharField(max_length=16, choices=TaxTreatment.choices, default=TaxTreatment.PENDING)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENT_VALIDATORS)
    general_discount_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0, validators=PERCENT_VALIDATORS)
    charge_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0, validators=PERCENT_VALIDATORS)
    fixed_charge = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    logistics_reserve = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rounding_increment = models.PositiveIntegerField(default=100)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProviderSkuAdjustment(models.Model):
    provider = models.ForeignKey(ProviderConfig, on_delete=models.CASCADE, related_name="sku_adjustments")
    sku = models.CharField(max_length=160)
    discount_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0, validators=PERCENT_VALIDATORS)
    fixed_charge = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    source_reference = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "sku"], name="unique_provider_sku_adjustment")]


class SupplierCatalogItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, related_name="catalog_items")
    source_batch = models.CharField(max_length=120)
    source_row = models.CharField(max_length=80, blank=True)
    supplier_code = models.CharField(max_length=160, blank=True)
    supplier_sku = models.CharField(max_length=160, blank=True, db_index=True)
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    supplier_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    derived_net_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    inventory = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)
    warehouse = models.CharField(max_length=160, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    missing_fields = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider__name", "supplier_sku", "source_row"]
        indexes = [models.Index(fields=["provider", "supplier_sku"])]


class SupplierCatalogImport(models.Model):
    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, related_name="catalog_imports")
    source_filename = models.CharField(max_length=300)
    source_path_at_import = models.TextField(blank=True)
    source_sha256 = models.CharField(max_length=64, unique=True)
    catalog_date = models.DateField()
    page_count = models.PositiveIntegerField()
    extracted_rows = models.PositiveIntegerField()
    unique_skus = models.PositiveIntegerField()
    duplicate_skus = models.PositiveIntegerField(default=0)
    invalid_prices = models.PositiveIntegerField(default=0)
    missing_skus = models.PositiveIntegerField(default=0)
    missing_descriptions = models.PositiveIntegerField(default=0)
    exact_shopify_matches = models.PositiveIntegerField(default=0)
    missing_shopify_matches = models.PositiveIntegerField(default=0)
    ambiguous_shopify_matches = models.PositiveIntegerField(default=0)
    tax_included_confirmed = models.BooleanField(default=False)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS)
    external_writes = models.PositiveSmallIntegerField(default=0)
    audit_payload = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]


class ProviderDataImport(models.Model):
    """Auditable local import of physical and inventory facts supplied out of band."""

    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, related_name="data_imports")
    source_filename = models.CharField(max_length=300)
    source_sha256 = models.CharField(max_length=64, unique=True)
    imported_rows = models.PositiveIntegerField(default=0)
    rejected_rows = models.PositiveIntegerField(default=0)
    weight_rows = models.PositiveIntegerField(default=0)
    dimension_rows = models.PositiveIntegerField(default=0)
    inventory_rows = models.PositiveIntegerField(default=0)
    audit_payload = models.JSONField(default=dict, blank=True)
    external_writes = models.PositiveSmallIntegerField(default=0)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]


class SupplierItemInventorySnapshot(models.Model):
    item = models.ForeignKey(SupplierCatalogItem, on_delete=models.CASCADE, related_name="inventory_snapshots")
    import_batch = models.ForeignKey(ProviderDataImport, on_delete=models.PROTECT, related_name="inventory_snapshots")
    warehouse_external_id = models.CharField(max_length=120, blank=True)
    warehouse_name = models.CharField(max_length=180)
    reported_stock = models.DecimalField(max_digits=14, decimal_places=3)
    reserved_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    safety_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    available_to_promise = models.DecimalField(max_digits=14, decimal_places=3)
    observed_at = models.DateTimeField()
    freshness_minutes = models.PositiveIntegerField(default=1440)
    update_method = models.CharField(max_length=32, default="FILE")
    evidence_reference = models.CharField(max_length=300)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "import_batch", "warehouse_external_id"], name="unique_supplier_inventory_import_warehouse")]


class MasterProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shopify_product_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    title = models.CharField(max_length=300)
    vendor = models.CharField(max_length=160, blank=True)
    brand = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=200, blank=True)
    product_type = models.CharField(max_length=200, blank=True)
    description_html = models.TextField(blank=True)
    status = models.CharField(max_length=32, default="DRAFT")
    tags = models.JSONField(default=list, blank=True)
    collections = models.JSONField(default=list, blank=True)
    quality_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    missing_fields = models.JSONField(default=list, blank=True)
    needs_review = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["status", "needs_review"], name="catalog_status_review_idx"),
            models.Index(fields=["vendor", "brand"], name="catalog_vendor_brand_idx"),
            models.Index(fields=["category", "quality_score"], name="catalog_category_quality_idx"),
        ]


class ProductVariant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=160, blank=True, db_index=True)
    title = models.CharField(max_length=300, blank=True)
    barcode = models.CharField(max_length=160, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    compare_at_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    provider_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    inventory_policy = models.CharField(max_length=32, blank=True)
    shopify_variant_id = models.CharField(max_length=80, blank=True, db_index=True)

    class Meta:
        ordering = ["sku", "title"]
        indexes = [
            models.Index(fields=["price"], name="catalog_variant_price_idx"),
        ]


class ProductImage(models.Model):
    product = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, related_name="images")
    # Las URLs CDN firmadas/sanitizadas del snapshot pueden superar el límite
    # implícito de 200; PostgreSQL lo aplica aunque SQLite no lo haga.
    source_url = models.URLField(max_length=2048)
    alt_text = models.CharField(max_length=300, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]


class InventoryLevel(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="inventory_levels")
    location_external_id = models.CharField(max_length=100)
    location_name = models.CharField(max_length=160)
    available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    observed_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["variant", "location_external_id"], name="unique_variant_location")]


class ProductMetafield(models.Model):
    product = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, related_name="metafields")
    namespace = models.CharField(max_length=120)
    key = models.CharField(max_length=120)
    value = models.TextField(blank=True)
    value_type = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "namespace", "key"], name="unique_product_metafield")]


class ChannelSnapshot(models.Model):
    product = models.ForeignKey(MasterProduct, on_delete=models.CASCADE, related_name="channel_snapshots")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="channel_snapshots", null=True, blank=True)
    channel = models.CharField(max_length=32, choices=Channel.choices)
    external_product_id = models.CharField(max_length=100, blank=True)
    external_variant_id = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=40, default="NOT_IMPORTED")
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    compare_at_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    inventory_available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quality_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(100)])
    payload = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["channel", "state"])]


class ExternalChannelProductSnapshot(models.Model):
    """Producto/publicación leído de un marketplace antes de conciliarlo.

    Conserva también los registros sin SKU o sin coincidencia. Una lectura de
    canal nunca crea silenciosamente un producto maestro.
    """

    class MatchStatus(models.TextChoices):
        EXACT_SKU = "EXACT_SKU", "SKU exacto Shopify"
        DUPLICATE_SKU = "DUPLICATE_SKU", "SKU duplicado en el canal"
        AMBIGUOUS_SKU = "AMBIGUOUS_SKU", "SKU ambiguo en Shopify"
        IDENTIFIER_REVIEW = "IDENTIFIER_REVIEW", "Otro identificador por revisar"
        MISSING_SHOPIFY = "MISSING_SHOPIFY", "Ausente en Shopify"
        MISSING_SKU = "MISSING_SKU", "Sin SKU verificable"
        STALE = "STALE", "Ausente en la lectura más reciente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(max_length=32, choices=Channel.choices)
    external_product_id = models.CharField(max_length=160)
    external_variant_id = models.CharField(max_length=160, blank=True)
    sku = models.CharField(max_length=160, blank=True, db_index=True)
    barcode = models.CharField(max_length=160, blank=True, db_index=True)
    title = models.CharField(max_length=500, blank=True)
    brand = models.CharField(max_length=180, blank=True)
    category = models.CharField(max_length=240, blank=True)
    state = models.CharField(max_length=80, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    inventory_available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    currency = models.CharField(max_length=3, default="COP")
    url = models.URLField(max_length=2048, blank=True)
    image_url = models.URLField(max_length=2048, blank=True)
    matched_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_channel_snapshots",
    )
    match_status = models.CharField(max_length=32, choices=MatchStatus.choices)
    match_reason = models.CharField(max_length=500, blank=True)
    candidate_variant_ids = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField()
    source_updated_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["channel", "sku", "external_product_id", "external_variant_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_product_id", "external_variant_id"],
                name="unique_external_channel_product_variant",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "match_status", "active"], name="catalog_ext_match_idx"),
            models.Index(fields=["channel", "sku"], name="catalog_ext_sku_idx"),
            models.Index(fields=["channel", "state"], name="catalog_ext_state_idx"),
        ]


class CostObservation(models.Model):
    class Source(models.TextChoices):
        PROVIDER_CATALOG = "PROVIDER_CATALOG", "Catálogo de proveedor"
        SIIGO = "SIIGO", "Siigo"
        SHOPIFY = "SHOPIFY", "Costo actual Shopify"
        MANUAL = "MANUAL", "Registro manual aprobado"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="cost_observations")
    source = models.CharField(max_length=32, choices=Source.choices)
    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, null=True, blank=True, related_name="cost_observations")
    raw_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    derived_net_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="COP")
    tax_treatment = models.CharField(max_length=16, choices=ProviderConfig.TaxTreatment.choices, default=ProviderConfig.TaxTreatment.PENDING)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENT_VALIDATORS)
    discount_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0, validators=PERCENT_VALIDATORS)
    observed_at = models.DateTimeField()
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    evidence_reference = models.CharField(max_length=300, blank=True)
    payload_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-observed_at", "source"]


class CanonicalCostSelection(models.Model):
    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="canonical_cost")
    observation = models.ForeignKey(CostObservation, on_delete=models.PROTECT, related_name="canonical_selections")
    policy_name = models.CharField(max_length=180)
    reason = models.TextField()
    discrepancy = models.JSONField(default=dict, blank=True)
    selected_at = models.DateTimeField(auto_now=True)


class CostSyncProposal(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        PREVIEWED = "PREVIEWED", "Vista previa"
        APPROVED = "APPROVED", "Aprobado internamente"
        EXECUTED = "EXECUTED", "Ejecutado"
        REVERSED = "REVERSED", "Revertido"

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="cost_sync_proposals")
    source_observation = models.ForeignKey(CostObservation, on_delete=models.PROTECT, related_name="sync_proposals")
    target = models.CharField(max_length=32, choices=[("SHOPIFY", "Shopify"), ("SIIGO", "Siigo")])
    previous_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    proposed_cost = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    preview_payload = models.JSONField(default=dict, blank=True)
    rollback_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SiigoProductSnapshot(models.Model):
    class MatchStatus(models.TextChoices):
        EXACT_SHOPIFY = "EXACT_SHOPIFY", "SKU exacto en Shopify"
        MISSING_SHOPIFY = "MISSING_SHOPIFY", "SKU ausente en Shopify"
        AMBIGUOUS_SHOPIFY = "AMBIGUOUS_SHOPIFY", "SKU ambiguo en Shopify"

    siigo_id = models.CharField(max_length=120, unique=True)
    sku = models.CharField(max_length=160, db_index=True)
    name = models.CharField(max_length=300)
    active = models.BooleanField(default=True)
    sale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    tax_included = models.BooleanField(null=True, blank=True)
    available_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    warehouses = models.JSONField(default=list, blank=True)
    matched_variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="siigo_snapshots")
    match_status = models.CharField(max_length=24, choices=MatchStatus.choices)
    cost_status = models.CharField(max_length=80, default="NOT_PROVIDED_BY_PRODUCT_LIST")
    observed_at = models.DateTimeField()
    source_updated_at = models.DateTimeField(null=True, blank=True)
    evidence_reference = models.CharField(max_length=300, default="Siigo API v1 product list")

    class Meta:
        ordering = ["sku"]
        indexes = [
            models.Index(fields=["match_status", "active"], name="catalog_siigo_match_idx"),
        ]


class InventorySourceSnapshot(models.Model):
    class UpdateMethod(models.TextChoices):
        API = "API", "API"
        FILE = "FILE", "Archivo"
        MANUAL = "MANUAL", "Actualización manual aprobada"
        CONNECTED_SEARCH_PARTIAL = "CONNECTED_SEARCH_PARTIAL", "Lectura conectada parcial"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="inventory_sources")
    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, null=True, blank=True, related_name="inventory_snapshots")
    source_name = models.CharField(max_length=160)
    warehouse_external_id = models.CharField(max_length=120, blank=True)
    warehouse_name = models.CharField(max_length=180, blank=True)
    reported_stock = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    reserved_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    safety_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    available_to_promise = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    stock_unknown = models.BooleanField(default=True)
    observed_at = models.DateTimeField()
    freshness_minutes = models.PositiveIntegerField(default=1440)
    update_method = models.CharField(max_length=32, choices=UpdateMethod.choices)
    canonical = models.BooleanField(default=False)
    evidence_reference = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["source_name", "warehouse_name"]
        indexes = [models.Index(fields=["variant", "canonical", "stock_unknown"])]


class ChannelInventoryAllocation(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="channel_allocations")
    channel = models.CharField(max_length=32, choices=Channel.choices)
    allocation_cap = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    allocated_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    source_available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    blocked_unknown_inventory = models.BooleanField(default=True)
    idempotency_key = models.CharField(max_length=120, unique=True)
    last_calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["variant", "channel"], name="unique_variant_channel_allocation")]


class SkuReconciliation(models.Model):
    class Status(models.TextChoices):
        EXACT = "EXACT", "Coincidencia exacta"
        DUPLICATE = "DUPLICATE", "SKU duplicado"
        MISSING = "MISSING", "Sin coincidencia"
        AMBIGUOUS = "AMBIGUOUS", "Coincidencia ambigua"

    supplier_item = models.ForeignKey(SupplierCatalogItem, on_delete=models.CASCADE, related_name="reconciliations")
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, related_name="supplier_matches", null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    candidate_variant_ids = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=300)
    reviewed = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "reviewed"], name="catalog_reconcile_idx"),
        ]


class PricingPolicy(models.Model):
    class Precedence(models.TextChoices):
        GENERAL = "GENERAL", "Regla general"
        SPECIFIC = "SPECIFIC", "Regla específica"
        EXCEPTION = "EXCEPTION", "Excepción"

    class ApprovalStatus(models.TextChoices):
        HYPOTHESIS = "HYPOTHESIS", "Hipótesis editable, no aprobada"
        APPROVED_LOCAL = "APPROVED_LOCAL", "Aprobada para simulación local"

    class ReserveBehavior(models.TextChoices):
        CAP = "CAP", "Tope de protección; no se suma automáticamente"
        INCLUDED_IN_PRICE = "INCLUDED_IN_PRICE", "Incluida en el precio propuesto"

    name = models.CharField(max_length=180)
    active = models.BooleanField(default=True)
    precedence = models.CharField(max_length=16, choices=Precedence.choices, default=Precedence.GENERAL)
    priority = models.IntegerField(default=100)
    channel = models.CharField(max_length=32, choices=Channel.choices, blank=True)
    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, null=True, blank=True, related_name="pricing_policies")
    collection = models.CharField(max_length=180, blank=True)
    brand = models.CharField(max_length=180, blank=True)
    category = models.CharField(max_length=180, blank=True)
    product_type = models.CharField(max_length=180, blank=True)
    sku = models.CharField(max_length=160, blank=True)
    combination = models.JSONField(default=dict, blank=True)
    target_margin_percent = models.DecimalField(max_digits=6, decimal_places=3, validators=PERCENT_VALIDATORS)
    channel_commission_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0, validators=PERCENT_VALIDATORS)
    channel_fixed_charge = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    logistics_reserve = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rounding_increment = models.PositiveIntegerField(default=100)
    max_shipping_subsidy = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    minimum_margin_percent = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True, validators=PERCENT_VALIDATORS)
    simulation_only = models.BooleanField(default=True)
    approval_status = models.CharField(max_length=24, choices=ApprovalStatus.choices, default=ApprovalStatus.HYPOTHESIS)
    reserve_behavior = models.CharField(max_length=24, choices=ReserveBehavior.choices, default=ReserveBehavior.CAP)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    explanation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "name"]


class PriceCalculation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="price_calculations", null=True, blank=True)
    channel = models.CharField(max_length=32, choices=Channel.choices)
    policy = models.ForeignKey(PricingPolicy, on_delete=models.PROTECT, related_name="calculations", null=True, blank=True)
    input_snapshot = models.JSONField(default=dict)
    formula = models.JSONField(default=dict)
    normalized_cost = models.DecimalField(max_digits=14, decimal_places=2)
    previous_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    proposed_price = models.DecimalField(max_digits=14, decimal_places=2)
    achieved_margin_percent = models.DecimalField(max_digits=7, decimal_places=3)
    commission_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    logistics_reserve = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    quoted_shipping = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    customer_shipping_charge = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shipping_subsidy = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rule_reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class IntegrationReadStatus(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Disponible"
        PARTIAL = "PARTIAL", "Parcial"
        MISSING = "MISSING", "Dato ausente"
        NOT_AUTHORIZED = "NOT_AUTHORIZED", "No autorizado"
        NOT_APPLICABLE = "NOT_APPLICABLE", "No aplica"
        BLOCKED = "BLOCKED", "Bloqueado"

    system = models.CharField(max_length=40)
    capability = models.CharField(max_length=100)
    status = models.CharField(max_length=24, choices=Status.choices)
    message = models.CharField(max_length=500)
    evidence_reference = models.CharField(max_length=300, blank=True)
    record_count = models.PositiveIntegerField(null=True, blank=True)
    observed_at = models.DateTimeField()
    last_success_at = models.DateTimeField(null=True, blank=True)
    external_writes = models.PositiveSmallIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["system", "capability"], name="unique_integration_capability")]
        ordering = ["system", "capability"]


class LogisticsQuoteSnapshot(models.Model):
    class Basis(models.TextChoices):
        CHECKOUT_ESTIMATE = "CHECKOUT_ESTIMATE", "Cotización estimada"
        REALIZED_GUIDE = "REALIZED_GUIDE", "Costo real de guía"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="logistics_quotes", null=True, blank=True)
    provider = models.CharField(max_length=40, default="ENVIA")
    basis = models.CharField(max_length=24, choices=Basis.choices)
    status = models.CharField(max_length=24, choices=IntegrationReadStatus.Status.choices)
    destination = models.JSONField(default=dict, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)
    carrier = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="COP")
    evidence_reference = models.CharField(max_length=300, blank=True)
    external_reference_hash = models.CharField(max_length=64, blank=True, db_index=True)
    order_reference_hash = models.CharField(max_length=64, blank=True, db_index=True)
    observed_at = models.DateTimeField()
    external_writes = models.PositiveSmallIntegerField(default=0)
    fingerprint = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-observed_at"]


class BulkSimulationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    basis = models.CharField(max_length=80, default="LOCAL_READ_ONLY")
    assumptions = models.JSONField(default=list, blank=True)
    metrics = models.JSONField(default=dict)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    external_writes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]


class ShopifyImportState(models.Model):
    class Status(models.TextChoices):
        NOT_CONFIGURED = "NOT_CONFIGURED", "Sin configurar"
        READY = "READY", "Preparada"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Finalizada"
        FAILED = "FAILED", "Fallida"

    key = models.CharField(max_length=40, unique=True, default="PRIMARY")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NOT_CONFIGURED)
    initial_cursor = models.TextField(blank=True)
    next_cursor = models.TextField(blank=True)
    updated_after = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    pages_processed = models.PositiveIntegerField(default=0)
    products_processed = models.PositiveIntegerField(default=0)
    variants_processed = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)


class WebhookInbox(models.Model):
    topic = models.CharField(max_length=100)
    external_event_id = models.CharField(max_length=180, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=32, default="PENDING")
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)


class ReviewBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        PREVIEWED = "PREVIEWED", "Vista previa"
        APPROVED = "APPROVED", "Aprobado internamente"
        REVERSED = "REVERSED", "Revertido"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    channel = models.CharField(max_length=32, choices=Channel.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    preview_payload = models.JSONField(default=dict, blank=True)
    rollback_payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ReviewBatchItem(models.Model):
    batch = models.ForeignKey(ReviewBatch, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="review_items")
    proposed_change = models.JSONField(default=dict)
    validation_messages = models.JSONField(default=list, blank=True)
    approved = models.BooleanField(default=False)


class PhysicalEvidenceCandidate(models.Model):
    class Field(models.TextChoices):
        WEIGHT = "WEIGHT", "Peso"
        LENGTH = "LENGTH", "Largo"
        WIDTH = "WIDTH", "Ancho"
        HEIGHT = "HEIGHT", "Alto"

    class Scope(models.TextChoices):
        PRODUCT = "PRODUCT", "Producto"
        PACKAGE = "PACKAGE", "Empaque para transporte"

    class Classification(models.TextChoices):
        CONFIRMED = "CONFIRMED", "Confirmado"
        DERIVED = "DERIVED", "Derivado de evidencia exacta"
        ESTIMATED = "ESTIMATED", "Estimado por producto similar"
        CONFLICT = "CONFLICT", "Conflicto entre fuentes"

    class SourceType(models.TextChoices):
        SHOPIFY_STRUCTURED = "SHOPIFY_STRUCTURED", "Shopify estructurado"
        SHOPIFY_METAFIELD = "SHOPIFY_METAFIELD", "Metacampo Shopify"
        SHOPIFY_DESCRIPTION = "SHOPIFY_DESCRIPTION", "Descripción Shopify"
        PROVIDER_CATALOG = "PROVIDER_CATALOG", "Catálogo proveedor"
        MANUFACTURER = "MANUFACTURER", "Fabricante oficial"
        PUBLIC_RETAIL_EXACT = "PUBLIC_RETAIL_EXACT", "Comercio público exacto"
        PUBLIC_SIMILAR = "PUBLIC_SIMILAR", "Producto público similar"
        MANUAL = "MANUAL", "Validación manual"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="physical_candidates", null=True, blank=True)
    supplier_item = models.ForeignKey(SupplierCatalogItem, on_delete=models.CASCADE, related_name="physical_candidates", null=True, blank=True)
    field = models.CharField(max_length=12, choices=Field.choices)
    scope = models.CharField(max_length=12, choices=Scope.choices)
    classification = models.CharField(max_length=16, choices=Classification.choices)
    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    source_url = models.URLField(max_length=700, blank=True)
    source_reference = models.CharField(max_length=500)
    matching_identifier_type = models.CharField(max_length=40, blank=True)
    matching_identifier_value = models.CharField(max_length=200, blank=True)
    evidence_excerpt = models.CharField(max_length=700)
    evidence_selector = models.CharField(max_length=300, blank=True)
    observed_at = models.DateTimeField()
    extraction_method = models.CharField(max_length=80)
    original_value = models.DecimalField(max_digits=14, decimal_places=4)
    original_unit = models.CharField(max_length=16)
    normalized_value = models.DecimalField(max_digits=14, decimal_places=4)
    normalized_unit = models.CharField(max_length=16)
    confidence = models.DecimalField(max_digits=5, decimal_places=4)
    conflict = models.BooleanField(default=False)
    conflict_details = models.JSONField(default=dict, blank=True)
    content_fingerprint = models.CharField(max_length=64, unique=True)
    stale_after = models.DateTimeField(null=True, blank=True)
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confidence", "field", "-observed_at"]
        indexes = [models.Index(fields=["variant", "scope", "classification"]), models.Index(fields=["supplier_item", "field"])]


class PhysicalEvidenceDecision(models.Model):
    class Action(models.TextChoices):
        APPROVE_LOCAL = "APPROVE_LOCAL", "Aprobar localmente"
        REJECT = "REJECT", "Rechazar"
        REQUEST_PROVIDER = "REQUEST_PROVIDER", "Pedir al proveedor"

    candidate = models.ForeignKey(PhysicalEvidenceCandidate, on_delete=models.PROTECT, related_name="decisions")
    action = models.CharField(max_length=24, choices=Action.choices)
    reason = models.CharField(max_length=500)
    actor_label = models.CharField(max_length=160, default="local-operator")
    decision_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    external_writes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]


class PhysicalEnrichmentPilotSelection(models.Model):
    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="physical_pilot_selection")
    supplier_item = models.ForeignKey(SupplierCatalogItem, on_delete=models.CASCADE, related_name="pilot_selections")
    score = models.PositiveIntegerField()
    criteria = models.JSONField(default=list)
    rank = models.PositiveSmallIntegerField()
    selected_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank"]


class ShopifyPhysicalUpdatePreview(models.Model):
    class Status(models.TextChoices):
        BLOCKED = "BLOCKED", "Bloqueado"
        READY_LOCAL = "READY_LOCAL", "Listo para revisión local"
        REJECTED = "REJECTED", "Rechazado localmente"

    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="physical_update_preview")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BLOCKED)
    previous_values = models.JSONField(default=dict)
    proposed_metafields = models.JSONField(default=dict)
    evidence_snapshot = models.JSONField(default=dict)
    blockers = models.JSONField(default=list)
    rollback_payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    external_writes = models.PositiveSmallIntegerField(default=0)


class EnviaQuoteContractRun(models.Model):
    request_fingerprint = models.CharField(max_length=64, unique=True)
    request_snapshot = models.JSONField(default=dict)
    response_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=24)
    fixture_name = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)
    external_writes = models.PositiveSmallIntegerField(default=0)


class PhysicalMeasurementImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Vista previa"
        IMPORTED_LOCAL = "IMPORTED_LOCAL", "Importado localmente"
        DEMO_VALIDATED = "DEMO_VALIDATED", "Demo validado y bloqueado"
        REVERSED = "REVERSED", "Revertido localmente"

    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, related_name="physical_measurement_imports")
    source_filename = models.CharField(max_length=300)
    source_sha256 = models.CharField(max_length=64)
    is_demo = models.BooleanField(default=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PREVIEW)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    conflict_rows = models.PositiveIntegerField(default=0)
    snapshot_before = models.JSONField(default=dict, blank=True)
    rollback_snapshot = models.JSONField(default=dict, blank=True)
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["provider", "source_sha256"], name="unique_physical_measurement_import")]


class PhysicalMeasurementImportRow(models.Model):
    class Status(models.TextChoices):
        VALID = "VALID", "Válida para revisión"
        ERROR = "ERROR", "Error de fila"
        CONFLICT = "CONFLICT", "Conflicto"
        IMPORTED_CONFIRMED = "IMPORTED_CONFIRMED", "Evidencia confirmada importada"
        DEMO_BLOCKED = "DEMO_BLOCKED", "Demo bloqueado"
        REVERSED = "REVERSED", "Revertida localmente"

    batch = models.ForeignKey(PhysicalMeasurementImportBatch, on_delete=models.CASCADE, related_name="rows")
    supplier_item = models.ForeignKey(SupplierCatalogItem, on_delete=models.PROTECT, related_name="physical_measurement_rows", null=True, blank=True)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="physical_measurement_rows", null=True, blank=True)
    row_number = models.PositiveIntegerField()
    sku = models.CharField(max_length=160, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices)
    raw_payload = models.JSONField(default=dict)
    normalized_payload = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    conflicts = models.JSONField(default=list, blank=True)
    candidate_ids = models.JSONField(default=list, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["row_number"]
        constraints = [models.UniqueConstraint(fields=["batch", "row_number"], name="unique_physical_measurement_row")]


class PhysicalMeasurementTask(models.Model):
    class Action(models.TextChoices):
        REQUEST_PROVIDER = "REQUEST_PROVIDER", "Solicitar al proveedor"
        REGISTER_MEASUREMENT = "REGISTER_MEASUREMENT", "Registrar medición"
        IMPORT_FILE = "IMPORT_FILE", "Importar archivo"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Pendiente"
        IN_REVIEW = "IN_REVIEW", "En revisión"
        COMPLETED = "COMPLETED", "Completada"
        CANCELLED = "CANCELLED", "Cancelada"

    supplier_item = models.ForeignKey(SupplierCatalogItem, on_delete=models.CASCADE, related_name="physical_tasks")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="physical_tasks")
    action = models.CharField(max_length=28, choices=Action.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    missing_fields = models.JSONField(default=list)
    note = models.CharField(max_length=500, blank=True)
    actor_label = models.CharField(max_length=160, default="local-operator")
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["variant", "action"], name="unique_physical_task_action")]


class CatalogHistoryEvent(models.Model):
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=120)
    action = models.CharField(max_length=100)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reversible = models.BooleanField(default=True)
    actor_label = models.CharField(max_length=160, default="local-demo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


# Fase 6: lote idempotente, reversible y limitado a precios en SQLite local.
class PricingLocalBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Vista previa"
        BLOCKED = "BLOCKED", "Bloqueado por datos o conflictos"
        APPLIED_LOCAL = "APPLIED_LOCAL", "Aplicado solo localmente"
        REVERSED = "REVERSED", "Revertido localmente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PREVIEW)
    selection_snapshot = models.JSONField(default=dict)
    rule_snapshot = models.JSONField(default=list)
    preview_payload = models.JSONField(default=dict)
    rollback_payload = models.JSONField(default=dict, blank=True)
    applied_payload = models.JSONField(default=dict, blank=True)
    actor_label = models.CharField(max_length=160, default="local-operator")
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    external_writes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]


# Fase 6: conserva cada comparación logística sin afirmar que el fixture sea tarifa actual.
class MultwarehouseSimulationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=64, unique=True)
    input_snapshot = models.JSONField(default=dict)
    result_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=32)
    quote_basis = models.CharField(max_length=64)
    actor_label = models.CharField(max_length=160, default="local-operator")
    created_at = models.DateTimeField(auto_now_add=True)
    external_writes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]


# Fase 8: identidad Sodimac gobernada por archivo; las observaciones públicas nunca son la fuente maestra.
class SodimacCatalogImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Vista previa"
        PREVIEW_PARTIAL = "PREVIEW_PARTIAL", "Vista previa parcial"
        BLOCKED = "BLOCKED", "Bloqueado por conflictos"
        APPLIED_LOCAL = "APPLIED_LOCAL", "Aplicado solo localmente"
        APPLIED_PARTIAL = "APPLIED_PARTIAL", "Aplicado parcialmente"
        REVERSED = "REVERSED", "Revertido localmente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_filename = models.CharField(max_length=300)
    source_sha256 = models.CharField(max_length=64, db_index=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    source_size_bytes = models.PositiveIntegerField(default=0)
    source_date = models.DateField(null=True, blank=True)
    header_mapping = models.JSONField(default=dict)
    allow_partial = models.BooleanField(default=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PREVIEW)
    is_fixture = models.BooleanField(default=False)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    conflict_rows = models.PositiveIntegerField(default=0)
    rejected_rows = models.PositiveIntegerField(default=0)
    applied_links = models.PositiveIntegerField(default=0)
    rollback_payload = models.JSONField(default=dict, blank=True)
    actor_label = models.CharField(max_length=160, default="local-operator")
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class SodimacCatalogLink(models.Model):
    class Status(models.TextChoices):
        UNLINKED = "UNLINKED", "Sin vínculo"
        LINKED_EXACT = "LINKED_EXACT", "Vínculo exacto"
        AMBIGUOUS = "AMBIGUOUS", "Ambiguo"
        STALE = "STALE", "Vencido"
        NOT_FOUND = "NOT_FOUND", "No encontrado"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Necesita revisión"

    class SourceKind(models.TextChoices):
        FILE = "CONFIRMED_BY_FILE", "Confirmado por archivo"
        MANUAL = "MANUAL_DECISION", "Decisión manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="sodimac_catalog_links")
    canonical_sku = models.CharField(max_length=160, db_index=True)
    sodimac_sku = models.CharField(max_length=160, db_index=True)
    listing_id = models.CharField(max_length=160, blank=True, db_index=True)
    listing_url = models.URLField(max_length=2048, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NEEDS_REVIEW)
    source_kind = models.CharField(max_length=32, choices=SourceKind.choices, default=SourceKind.FILE)
    source_checksum = models.CharField(max_length=64)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    evidence = models.JSONField(default=dict, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    manual_decision = models.BooleanField(default=False)
    created_by_batch = models.ForeignKey(SodimacCatalogImportBatch, on_delete=models.PROTECT, related_name="created_links")
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["canonical_sku", "sodimac_sku", "listing_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["canonical_sku", "sodimac_sku", "listing_id"],
                name="unique_sodimac_canonical_listing",
            )
        ]
        indexes = [models.Index(fields=["status", "active", "last_verified_at"])]


class SodimacCatalogImportRow(models.Model):
    batch = models.ForeignKey(SodimacCatalogImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    canonical_sku = models.CharField(max_length=160, blank=True)
    sodimac_sku = models.CharField(max_length=160, blank=True)
    listing_id = models.CharField(max_length=160, blank=True)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="sodimac_import_rows")
    link = models.ForeignKey(SodimacCatalogLink, on_delete=models.SET_NULL, null=True, blank=True, related_name="import_rows")
    status = models.CharField(max_length=24)
    raw_payload = models.JSONField(default=dict)
    normalized_payload = models.JSONField(default=dict)
    errors = models.JSONField(default=list, blank=True)
    conflicts = models.JSONField(default=list, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["row_number"]


class SodimacCatalogObservation(models.Model):
    class EvidenceClass(models.TextChoices):
        FILE = "CONFIRMED_BY_FILE", "Confirmado por archivo"
        API = "CONFIRMED_BY_API", "Confirmado por API"
        PUBLIC = "OBSERVED_PUBLIC_PAGE", "Observado en página pública"
        INFERRED = "INFERRED", "Inferido"
        UNKNOWN = "UNKNOWN", "Desconocido"

    link = models.ForeignKey(SodimacCatalogLink, on_delete=models.CASCADE, related_name="observations")
    evidence_class = models.CharField(max_length=32, choices=EvidenceClass.choices)
    observed_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    source_reference = models.CharField(max_length=500, blank=True)
    source_fingerprint = models.CharField(max_length=64)
    publication_state = models.CharField(max_length=80, default="UNKNOWN")
    inventory_available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    inventory_source = models.CharField(max_length=32, default="UNKNOWN")
    raw_payload = models.JSONField(default=dict, blank=True)
    field_comparison = models.JSONField(default=dict, blank=True)
    dimension_scores = models.JSONField(default=dict, blank=True)
    overall_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    severity = models.CharField(max_length=16, default="BLOCKER")
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["link", "source_fingerprint"], name="unique_sodimac_observation_fingerprint")]


class SodimacAuditTask(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "En cola"
        COMPLETED = "COMPLETED", "Completada"
        BACKOFF = "BACKOFF", "En espera"
        MANUAL_REQUIRED = "MANUAL_REQUIRED", "Revisión manual"
        BLOCKED = "BLOCKED", "Bloqueada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(SodimacCatalogLink, on_delete=models.CASCADE, related_name="audit_tasks")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED)
    reason = models.CharField(max_length=160)
    priority = models.PositiveSmallIntegerField(default=50)
    input_fingerprint = models.CharField(max_length=64)
    cache_key = models.CharField(max_length=120)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    next_attempt_at = models.DateTimeField()
    last_error_code = models.CharField(max_length=80, blank=True)
    last_success_observation = models.ForeignKey(SodimacCatalogObservation, on_delete=models.SET_NULL, null=True, blank=True, related_name="completed_tasks")
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "next_attempt_at", "created_at"]
        constraints = [models.UniqueConstraint(fields=["link", "input_fingerprint"], name="unique_sodimac_audit_input")]


class SodimacKitImportBatch(models.Model):
    class Status(models.TextChoices):
        APPLIED_LOCAL = "APPLIED_LOCAL", "Aplicado solo localmente"
        REVERSED = "REVERSED", "Revertido localmente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_filename = models.CharField(max_length=300)
    source_sha256 = models.CharField(max_length=64, db_index=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    source_size_bytes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.APPLIED_LOCAL)
    kit_count = models.PositiveIntegerField(default=0)
    component_rows = models.PositiveIntegerField(default=0)
    resolved_kits = models.PositiveIntegerField(default=0)
    review_kits = models.PositiveIntegerField(default=0)
    exact_components = models.PositiveIntegerField(default=0)
    missing_components = models.PositiveIntegerField(default=0)
    ambiguous_components = models.PositiveIntegerField(default=0)
    rollback_payload = models.JSONField(default=dict, blank=True)
    actor_label = models.CharField(max_length=160, default="local-operator")
    external_writes = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class SodimacKit(models.Model):
    class Status(models.TextChoices):
        RESOLVED = "RESOLVED", "Componentes resueltos"
        PARTIAL = "PARTIAL", "Componentes por revisar"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sodimac_kit_sku = models.CharField(max_length=160, db_index=True)
    canonical_sku = models.CharField(max_length=160, blank=True, db_index=True)
    canonical_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sodimac_kits",
    )
    ean = models.CharField(max_length=160, blank=True, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices)
    active = models.BooleanField(default=True)
    source_checksum = models.CharField(max_length=64)
    created_by_batch = models.ForeignKey(SodimacKitImportBatch, on_delete=models.PROTECT, related_name="kits")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sodimac_kit_sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by_batch", "sodimac_kit_sku"],
                name="unique_sodimac_kit_per_batch",
            ),
        ]
        indexes = [models.Index(fields=["active", "sodimac_kit_sku"], name="catalog_sod_kit_active_idx")]


class SodimacKitComponent(models.Model):
    class MatchStatus(models.TextChoices):
        EXACT_SKU = "EXACT_SKU", "SKU PAMO exacto"
        AMBIGUOUS_SKU = "AMBIGUOUS_SKU", "SKU PAMO ambiguo"
        MISSING_SHOPIFY = "MISSING_SHOPIFY", "SKU PAMO ausente"

    kit = models.ForeignKey(SodimacKit, on_delete=models.CASCADE, related_name="components")
    row_number = models.PositiveIntegerField()
    component_sku = models.CharField(max_length=160, db_index=True)
    component_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sodimac_kit_components",
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    match_status = models.CharField(max_length=24, choices=MatchStatus.choices)
    candidate_variant_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kit__sodimac_kit_sku", "row_number"]
        constraints = [
            models.UniqueConstraint(fields=["kit", "component_sku"], name="unique_sodimac_kit_component"),
        ]


class ShopifySyncPolicy(models.Model):
    """Fail-closed policy for the local catalog -> Shopify outbox."""

    class Environment(models.TextChoices):
        BETA = "BETA", "Beta"
        PRODUCTION = "PRODUCTION", "Producción"

    key = models.CharField(max_length=32, unique=True, default="PRIMARY")
    environment = models.CharField(max_length=16, choices=Environment.choices, default=Environment.BETA)
    scan_enabled = models.BooleanField(default=True)
    writes_enabled = models.BooleanField(default=False)
    price_enabled = models.BooleanField(default=True)
    inventory_enabled = models.BooleanField(default=True)
    maximum_batch_size = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(25)])
    debounce_seconds = models.PositiveIntegerField(default=60)
    source_max_age_minutes = models.PositiveIntegerField(default=360)
    allowlisted_skus = models.JSONField(default=list, blank=True)
    updated_by = models.CharField(max_length=160, default="local-operator")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]


class ShopifySyncRun(models.Model):
    class Mode(models.TextChoices):
        SCAN = "SCAN", "Detección local"
        PREVIEW = "PREVIEW", "Vista previa"
        EXECUTE = "EXECUTE", "Ejecución Shopify"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completada"
        PARTIAL = "PARTIAL", "Parcial"
        BLOCKED = "BLOCKED", "Bloqueada"
        FAILED = "FAILED", "Fallida"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mode = models.CharField(max_length=16, choices=Mode.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField(max_length=40, default="MANUAL_PREVIEW")
    requested_skus = models.JSONField(default=list, blank=True)
    scanned_count = models.PositiveIntegerField(default=0)
    ready_count = models.PositiveIntegerField(default=0)
    blocked_count = models.PositiveIntegerField(default=0)
    no_change_count = models.PositiveIntegerField(default=0)
    succeeded_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    external_writes = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class ShopifySyncItem(models.Model):
    class Status(models.TextChoices):
        READY = "READY", "Lista para piloto"
        BLOCKED = "BLOCKED", "Bloqueada"
        NO_CHANGE = "NO_CHANGE", "Sin cambios"
        SUCCEEDED = "SUCCEEDED", "Sincronizada"
        FAILED = "FAILED", "Fallida"
        CONFLICT = "CONFLICT", "Conflicto concurrente"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(ShopifySyncRun, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="shopify_sync_items")
    sku = models.CharField(max_length=160, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    fields = models.JSONField(default=list, blank=True)
    previous_values = models.JSONField(default=dict, blank=True)
    proposed_values = models.JSONField(default=dict, blank=True)
    source_evidence = models.JSONField(default=dict, blank=True)
    blockers = models.JSONField(default=list, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    rollback_payload = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    external_writes = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    last_error_message = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["run", "sku"]
        constraints = [
            models.UniqueConstraint(fields=["run", "variant"], name="unique_shopify_sync_variant_run"),
        ]
