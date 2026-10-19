from rest_framework import serializers

from products.models import Marketplace

from .models import Invoice


class InvoiceListQuerySerializer(serializers.Serializer):
    """Filtros de `GET /api/invoicing/`. Fechas inclusive sobre la fecha
    de la factura. La página (`page`) la valida el paginador."""

    status = serializers.ChoiceField(choices=Invoice.Status.choices, required=False)
    marketplace = serializers.ChoiceField(choices=Marketplace.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=64, default="")
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50, default=20)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_to": "Debe ser igual o posterior a date_from."})
        return attrs
