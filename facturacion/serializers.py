from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .models import (
    AuthorizedPerson,
    Remittance,
    RemittanceDelivery,
    RemittanceFavorite,
    RemittanceLine,
    RemittanceParty,
    RemittanceSupplierInvoiceFile,
    RemittanceUsageDestination,
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


class AuthorizedPersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthorizedPerson
        fields = ["id", "customer", "name", "document", "phone"]


class UsageDestinationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceUsageDestination
        fields = ["id", "customer", "value"]


class RemittanceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceLine
        fields = [
            "id", "line_number", "quantity", "original_description", "usage_destination",
            "supplier_sku", "supplier_unit_cost", "supplier_line_total",
            "supplier_discount_percent", "supplier_discount_value",
            "siigo_sku", "invoice_description",
            "invoice_unit_price", "override_reason",
        ]
        read_only_fields = ["id", "line_number"]

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        include_accounting = self.context.get("include_accounting", False)
        if not include_accounting:
            for field in [
                "supplier_sku", "supplier_unit_cost", "supplier_line_total",
                "supplier_discount_percent", "supplier_discount_value", "siigo_sku",
                "invoice_description", "invoice_unit_price", "override_reason",
            ]:
                representation.pop(field, None)
        return representation


class RemittanceDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceDelivery
        fields = ["method", "provider_name", "tracking_number", "driver_name", "notes", "recipient_name", "completed_at"]
        read_only_fields = ["recipient_name", "completed_at"]


class RemittanceSupplierInvoiceFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemittanceSupplierInvoiceFile
        fields = ["id", "original_name", "mime_type", "size_bytes", "sha256", "created_at"]
        read_only_fields = fields


class RemittanceSerializer(serializers.ModelSerializer):
    warehouse_detail = WarehouseSerializer(source="warehouse", read_only=True)
    supplier_detail = PartySerializer(source="supplier", read_only=True)
    customer_detail = PartySerializer(source="customer", read_only=True)
    lines = RemittanceLineSerializer(many=True)
    delivery = RemittanceDeliverySerializer()
    supplier_invoice_files = RemittanceSupplierInvoiceFileSerializer(many=True, read_only=True)
    signature_status = serializers.SerializerMethodField()
    signed_by = serializers.SerializerMethodField()
    signed_at = serializers.SerializerMethodField()

    class Meta:
        model = Remittance
        fields = [
            "id", "number", "version", "warehouse", "warehouse_detail", "supplier", "supplier_detail",
            "customer", "customer_detail", "requester_name", "requester_document", "document_status",
            "delivery_status", "invoice_status", "communication_status", "lines", "delivery",
            "supplier_global_discount_percent", "supplier_global_discount_value",
            "supplier_other_charges", "supplier_freight_cost", "supplier_invoice_files",
            "signature_status", "signed_by", "signed_at", "created_at", "updated_at", "confirmed_at",
        ]
        read_only_fields = [
            "id", "number", "version", "document_status", "delivery_status", "invoice_status",
            "communication_status", "supplier_invoice_files", "created_at", "updated_at", "confirmed_at",
            "signature_status", "signed_by", "signed_at",
        ]

    def get_signature_status(self, instance):
        return "SIGNED" if hasattr(instance, "recipient_acceptance") else "PENDING"

    def get_signed_by(self, instance):
        acceptance = getattr(instance, "recipient_acceptance", None)
        return acceptance.signer_name if acceptance else None

    def get_signed_at(self, instance):
        acceptance = getattr(instance, "recipient_acceptance", None)
        return acceptance.signed_at if acceptance else None

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if not self.context.get("include_accounting", False):
            for field in [
                "supplier_global_discount_percent", "supplier_global_discount_value",
                "supplier_other_charges", "supplier_freight_cost", "supplier_invoice_files",
            ]:
                representation.pop(field, None)
        return representation

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
            destination = str(line_data.get("usage_destination") or "").strip().upper()
            if destination:
                RemittanceUsageDestination.objects.get_or_create(
                    customer=remittance.customer,
                    value=destination,
                )
        RemittanceDelivery.objects.create(remittance=remittance, **delivery_data)

        requester_name = remittance.requester_name.strip().upper()
        authorized_person = AuthorizedPerson.objects.filter(
            customer=remittance.customer,
            name=requester_name,
        ).order_by("id").first()
        if authorized_person is None:
            AuthorizedPerson.objects.create(
                customer=remittance.customer,
                name=requester_name,
                document=remittance.requester_document,
            )
        elif remittance.requester_document and not authorized_person.document:
            authorized_person.document = remittance.requester_document
            authorized_person.save(update_fields=["document"])
        return remittance


class AccountingPrivateLineSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    supplier_sku = serializers.CharField(max_length=120, allow_blank=True, required=False)
    supplier_unit_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    supplier_line_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    supplier_discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=0, max_value=100, allow_null=True, required=False,
    )
    supplier_discount_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )


class AccountingPrivateAdjustmentsSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    supplier_global_discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=0, max_value=100, allow_null=True, required=False,
    )
    supplier_global_discount_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    supplier_other_charges = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    supplier_freight_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    lines = AccountingPrivateLineSerializer(many=True)

    def validate_lines(self, value):
        line_ids = [line["id"] for line in value]
        if len(line_ids) != len(set(line_ids)):
            raise serializers.ValidationError("No repitas productos en los ajustes privados.")
        return value


class AccountingCommercialLineSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    siigo_sku = serializers.CharField(max_length=120, allow_blank=True)
    invoice_description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    invoice_margin_percent = serializers.DecimalField(
        max_digits=6,
        decimal_places=3,
        min_value=Decimal("0"),
        max_value=Decimal("99.999"),
    )
    override_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AccountingCommercialPreparationSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    default_margin_percent = serializers.DecimalField(
        max_digits=6,
        decimal_places=3,
        min_value=Decimal("0"),
        max_value=Decimal("99.999"),
    )
    lines = AccountingCommercialLineSerializer(many=True)

    def validate_lines(self, value):
        line_ids = [line["id"] for line in value]
        if len(line_ids) != len(set(line_ids)):
            raise serializers.ValidationError("No repitas productos en la preparación comercial.")
        return value
