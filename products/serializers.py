from rest_framework import serializers

from .models import KitComponent, Marketplace, MarketplaceSku, Product


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


# Tope de filas por carga de SKU (supuesto del plan, 2026-10-02).
SKU_UPLOAD_MAX_ROWS = 2000
SKU_UPLOAD_REQUIRED_COLUMNS = ("sku_pamo", "sku_marketplace")


class SkuUploadCreateSerializer(serializers.Serializer):
    """Cuerpo de `POST /api/products/sku-uploads/`: `{"marketplace", "rows"}`.
    Valida la forma (marketplace, tope de filas y que cada fila traiga las
    columnas obligatorias); los valores los valida el proceso fila por fila."""

    marketplace = serializers.ChoiceField(choices=Marketplace.choices)
    rows = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    def validate_rows(self, rows):
        if len(rows) > SKU_UPLOAD_MAX_ROWS:
            raise serializers.ValidationError(f"Máximo {SKU_UPLOAD_MAX_ROWS} filas por carga.")
        for number, row in enumerate(rows, start=2):
            missing = [column for column in SKU_UPLOAD_REQUIRED_COLUMNS if column not in row]
            if missing:
                raise serializers.ValidationError(f"Fila {number}: falta la columna {', '.join(missing)}.")
        return rows
