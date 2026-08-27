from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogo.models import (
    CatalogHistoryEvent,
    CanonicalCostSelection,
    ChannelSnapshot,
    CostObservation,
    InventorySourceSnapshot,
    InventoryLevel,
    MasterProduct,
    PriceCalculation,
    PricingPolicy,
    ProductImage,
    ProductVariant,
    ProviderConfig,
    ShopifyImportState,
)


class Command(BaseCommand):
    help = "Crea datos completamente ficticios para el laboratorio local de catálogo."

    def handle(self, *args, **options):
        fake_product_ids = ProductVariant.objects.filter(
            sku__startswith="BARU-DEMO-",
        ).values_list("product_id", flat=True)
        MasterProduct.objects.filter(pk__in=fake_product_ids).delete()
        ProviderConfig.objects.filter(name="Barú · demostración local").delete()
        confirmed_provider, _ = ProviderConfig.objects.update_or_create(
            name="Proveedor QA ficticio",
            defaults={
                "source_reference": "Asistente local de reglas",
                "tax_treatment": ProviderConfig.TaxTreatment.INCLUDED,
                "tax_rate": Decimal("19"),
                "general_discount_percent": Decimal("5"),
                "charge_percent": Decimal("1.5"),
                "fixed_charge": Decimal("1200"),
                "logistics_reserve": Decimal("3500"),
                "rounding_increment": 100,
            },
        )
        policy, _ = PricingPolicy.objects.update_or_create(
            name="Shopify general QA",
            defaults={
                "precedence": PricingPolicy.Precedence.GENERAL,
                "priority": 100,
                "channel": "SHOPIFY",
                "provider": confirmed_provider,
                "target_margin_percent": Decimal("30"),
                "channel_commission_percent": Decimal("2"),
                "channel_fixed_charge": Decimal("800"),
                "logistics_reserve": Decimal("3500"),
                "rounding_increment": 100,
                "max_shipping_subsidy": Decimal("12000"),
                "explanation": "Regla general Shopify del proveedor QA; no existe una excepción por SKU más específica.",
            },
        )

        products = []
        for index in range(4, 7):
            sku = f"QA-SKU-{index:03d}"
            product, _ = MasterProduct.objects.update_or_create(
                title=f"Producto multicanal simulado {index}",
                defaults={
                    "vendor": confirmed_provider.name,
                    "brand": "Marca demo A" if index % 2 else "Marca demo B",
                    "category": "Accesorios",
                    "product_type": "Accesorio",
                    "status": "ACTIVE" if index % 3 else "DRAFT",
                    "tags": ["demo", "local"],
                    "collections": ["Colección local"],
                    "quality_score": 48 + index * 7,
                    "missing_fields": [],
                    "needs_review": False,
                },
            )
            variant, _ = ProductVariant.objects.update_or_create(
                product=product,
                sku=sku,
                defaults={
                    "title": "Variante única",
                    "price": Decimal("199000") + index * Decimal("10000"),
                    "compare_at_price": Decimal("229000") + index * Decimal("10000"),
                    "provider_cost": Decimal("89000") + index * Decimal("7000"),
                    "shopify_variant_id": f"LOCAL-VARIANT-{index}",
                },
            )
            InventoryLevel.objects.update_or_create(
                variant=variant,
                location_external_id="LOCAL-LOCATION",
                defaults={"location_name": "Ubicación simulada", "available": index * 3, "observed_at": timezone.now()},
            )
            ProductImage.objects.get_or_create(
                product=product,
                source_url="http://127.0.0.1:5173/favicon.svg",
                defaults={"alt_text": "Imagen simulada; no corresponde a producto real", "position": 1},
            )
            ChannelSnapshot.objects.update_or_create(
                product=product,
                variant=variant,
                channel="SHOPIFY",
                defaults={
                    "external_product_id": f"LOCAL-PRODUCT-{index}",
                    "external_variant_id": f"LOCAL-VARIANT-{index}",
                    "state": "LOCAL_SNAPSHOT",
                    "price": variant.price,
                    "compare_at_price": variant.compare_at_price,
                    "cost": None,
                    "inventory_available": index * 3,
                    "quality_score": product.quality_score,
                    "payload": {"fixture": True},
                    "observed_at": timezone.now() - timedelta(hours=index),
                },
            )
            supplier_observation, _ = CostObservation.objects.update_or_create(
                variant=variant,
                source=CostObservation.Source.PROVIDER_CATALOG,
                provider=confirmed_provider,
                evidence_reference="Fixture local de catálogo de proveedor",
                defaults={
                    "raw_cost": variant.provider_cost,
                    "tax_treatment": ProviderConfig.TaxTreatment.INCLUDED,
                    "tax_rate": Decimal("19"),
                    "discount_percent": Decimal("5"),
                    "observed_at": timezone.now(),
                },
            )
            CostObservation.objects.update_or_create(
                variant=variant,
                source=CostObservation.Source.SHOPIFY,
                evidence_reference="Fixture local; costo Shopify no observado",
                defaults={
                    "raw_cost": None,
                    "tax_treatment": ProviderConfig.TaxTreatment.PENDING,
                    "observed_at": timezone.now(),
                },
            )
            CanonicalCostSelection.objects.update_or_create(
                variant=variant,
                defaults={
                    "observation": supplier_observation,
                    "policy_name": "Proveedor aprobado para SKU de demostración",
                    "reason": "Se selecciona el catálogo porque Siigo no aporta un costo verificable en la lectura de productos.",
                    "discrepancy": {"siigo": "NOT_PROVIDED", "shopify": "NOT_OBSERVED"},
                },
            )
            InventorySourceSnapshot.objects.update_or_create(
                variant=variant,
                source_name="Proveedor QA ficticio",
                warehouse_external_id="LOCAL-QA",
                defaults={
                    "provider": confirmed_provider,
                    "warehouse_name": "Bodega simulada",
                    "reported_stock": index * 3,
                    "reserved_stock": 0,
                    "safety_stock": 0,
                    "available_to_promise": index * 3,
                    "stock_unknown": False,
                    "observed_at": timezone.now(),
                    "freshness_minutes": 1440,
                    "update_method": InventorySourceSnapshot.UpdateMethod.FILE,
                    "canonical": True,
                    "evidence_reference": "Fixture local",
                },
            )
            products.append((product, variant))

        ShopifyImportState.objects.get_or_create(key="PRIMARY", defaults={"status": "NOT_CONFIGURED"})
        if not PriceCalculation.objects.exists():
            PriceCalculation.objects.create(
                variant=products[-1][1], channel="SHOPIFY", policy=policy,
                input_snapshot={"fixture": True},
                formula={"plain_language": "Estimación local, no utilidad realizada."},
                normalized_cost=Decimal("101000"), previous_price=Decimal("199000"), proposed_price=Decimal("209000"),
                achieved_margin_percent=Decimal("31.1"), commission_amount=Decimal("4180"),
                logistics_reserve=Decimal("3500"), quoted_shipping=Decimal("16500"),
                customer_shipping_charge=Decimal("3000"), shipping_subsidy=Decimal("13500"),
                rule_reason="Fixture local para el panel ejecutivo.",
            )
        CatalogHistoryEvent.objects.update_or_create(
            entity_type="catalog",
            entity_id="LOCAL-FIXTURE-001",
            action="seed_demo_created",
            defaults={"after": {"products": len(products), "supplier_rows": 0}, "reversible": True},
        )
        self.stdout.write(self.style.SUCCESS("Datos ficticios locales creados. No se consultó ni modificó ningún sistema externo."))
