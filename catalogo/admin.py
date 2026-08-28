from django.contrib import admin

from .models import MasterProduct, PriceCalculation, PricingPolicy, ProviderConfig, SupplierCatalogItem


admin.site.register([ProviderConfig, SupplierCatalogItem, MasterProduct, PricingPolicy, PriceCalculation])
