from django.db import transaction
from rest_framework import serializers

from .models import (
    Remittance,
    RemittanceDelivery,
    RemittanceFavorite,
    RemittanceLine,
    RemittanceParty,
    RemittanceWarehouse,
)


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceWarehouse
        fields = ["id", "name", "is_default"]


class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceParty
        fields = ["id", "party_type", "siigo_id", "nit", "name", "is_validated"]


class FavoriteSerializer(serializers.ModelSerializer):
    party = PartySerializer(read_only=True)

    class Meta:
        model = RemittanceFavorite
        fields = ["id", "party", "sort_order", "requires_validation"]


class RemittanceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceLine
        fields = [
            "id", "line_number", "quantity", "original_description", "usage_destination",
            "supplier_sku", "supplier_unit_cost", "siigo_sku", "invoice_description",
            "invoice_unit_price", "override_reason",
        ]
        read_only_fields = ["id", "line_number"]

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if not self.context.get("include_accounting", False):
            for field in [
                "supplier_sku", "supplier_unit_cost", "siigo_sku",
                "invoice_description", "invoice_unit_price", "override_reason",
            ]:
                representation.pop(field, None)
        return representation


class RemittanceDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceDelivery
        fields = ["method", "provider_name", "tracking_number", "driver_name", "notes", "completed_at"]
        read_only_fields = ["completed_at"]


class RemittanceSerializer(serializers.ModelSerializer):
    warehouse_detail = WarehouseSerializer(source="warehouse", read_only=True)
    supplier_detail = PartySerializer(source="supplier", read_only=True)
    customer_detail = PartySerializer(source="customer", read_only=True)
    lines = RemittanceLineSerializer(many=True)
    delivery = RemittanceDeliverySerializer()

    class Meta:
        model = Remittance
        fields = [
            "id", "number", "version", "warehouse", "warehouse_detail", "supplier", "supplier_detail",
            "customer", "customer_detail", "requester_name", "requester_document", "document_status",
            "delivery_status", "invoice_status", "communication_status", "lines", "delivery",
            "created_at", "updated_at", "confirmed_at",
        ]
        read_only_fields = [
            "id", "number", "version", "document_status", "delivery_status", "invoice_status",
            "communication_status", "created_at", "updated_at", "confirmed_at",
        ]

    def validate(self, attrs):
        supplier = attrs.get("supplier")
        customer = attrs.get("customer")
        if supplier and supplier.party_type != RemittanceParty.PartyType.SUPPLIER:
            raise serializers.ValidationError({"supplier": "El tercero debe ser proveedor."})
        if customer and customer.party_type != RemittanceParty.PartyType.CUSTOMER:
            raise serializers.ValidationError({"customer": "El tercero debe ser cliente."})
        if not attrs.get("lines"):
            raise serializers.ValidationError({"lines": "Agrega al menos un producto."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines_data = validated_data.pop("lines")
        delivery_data = validated_data.pop("delivery")
        remittance = Remittance.objects.create(created_by=self.context["request"].user, **validated_data)
        for index, line_data in enumerate(lines_data, start=1):
            RemittanceLine.objects.create(remittance=remittance, line_number=index, **line_data)
        RemittanceDelivery.objects.create(remittance=remittance, **delivery_data)
        return remittance
