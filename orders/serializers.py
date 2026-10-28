from rest_framework import serializers

from .functions.shopify_order_marketplace import MARKETPLACE_FILTERS
from .models import MarketplaceOrder, SodimacRpaRun, SodimacRpaRunItem


class OrderListQuerySerializer(serializers.Serializer):
    """Filtros de `GET /api/orders/`. Fechas en días de Colombia,
    inclusive. La página (`page`) la valida el paginador."""

    marketplace = serializers.ChoiceField(choices=MARKETPLACE_FILTERS, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=64, default="")
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50, default=20)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_to": "Debe ser igual o posterior a date_from."})
        return attrs


class SodimacRpaRunsQuerySerializer(serializers.Serializer):
    """Filtros de `GET /api/orders/sodimac/rpa/runs/`."""

    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=20)


class SodimacRpaRunItemSerializer(serializers.ModelSerializer):
    order_name = serializers.CharField(source="order.shopify_order_name", default="")
    status_label = serializers.CharField(source="get_status_display")
    result_label = serializers.CharField(source="get_result_display")

    class Meta:
        model = SodimacRpaRunItem
        fields = [
            "purchase_order", "order_name", "status", "status_label", "result", "result_label", "note",
            "tracking_number", "updated_at",
        ]


class SodimacRpaRunSerializer(serializers.ModelSerializer):
    """Una corrida del historial del RPA con sus OC y el conteo por estado
    (`summary`: solicitada / recibida / error / manual)."""

    source_label = serializers.CharField(source="get_source_display")
    status_label = serializers.CharField(source="get_status_display")
    requested_by = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    items = SodimacRpaRunItemSerializer(many=True)

    class Meta:
        model = SodimacRpaRun
        fields = [
            "id", "source", "source_label", "requested_by", "status", "status_label", "started_at", "finished_at",
            "summary", "items",
        ]

    def get_requested_by(self, run):
        user = run.requested_by
        return (user.email or user.get_username()) if user else ""

    def get_summary(self, run):
        counts = {status: 0 for status in MarketplaceOrder.LabelRequestStatus.values}
        for item in run.items.all():
            counts[item.status] = counts.get(item.status, 0) + 1
        return {"total": sum(counts.values()), **counts}
