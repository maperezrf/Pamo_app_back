from .base import ReadOnlyOrdersProvider


class ShopifyOrdersProvider(ReadOnlyOrdersProvider):
    provider = "shopify"

