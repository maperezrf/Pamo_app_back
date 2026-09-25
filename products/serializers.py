from rest_framework import serializers

from .models import KitComponent, MarketplaceSku, Product


class MarketplaceSkuSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketplaceSku
        fields = ["marketplace", "sku", "ean"]


class KitComponentSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="component.sku")

    class Meta:
        model = KitComponent
        fields = ["sku", "quantity"]


class ProductSerializer(serializers.ModelSerializer):
    marketplace_skus = MarketplaceSkuSerializer(many=True)
    components = KitComponentSerializer(many=True)

    class Meta:
        model = Product
        fields = ["sku", "name", "is_kit", "marketplace_skus", "components"]


class CatalogRowsSerializer(serializers.Serializer):
    """Cuerpo de una carga: `{"rows": [{...}, ...]}` con las filas que el
    frontend leyó del Excel. El contenido de cada fila lo valida la función
    de carga (columnas y valores)."""

    rows = serializers.ListField(child=serializers.DictField(), allow_empty=False)
