from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from pedidos.models import (
    IntegrationStatus,
    MessagingConfig,
    MessagingContact,
    Order,
    OrderItem,
    Shipment,
    ShipmentItem,
    TrackingEvent,
    WarehouseLocation,
)


User = get_user_model()


class Command(BaseCommand):
    help = "Crea datos sanitizados e idempotentes para revisar Pedidos en local."

    def handle(self, *args, **options):
        operations_group, _ = Group.objects.get_or_create(name="Operaciones")
        catalog_group, _ = Group.objects.get_or_create(name="Catalogo")
        user, _ = User.objects.get_or_create(
            username="operador.local@pamo.test",
            defaults={"email": "operador.local@pamo.test", "first_name": "Operador"},
        )
        # La copia integrada necesita ver Pedidos y Catalogo sin elevar el
        # usuario de demostracion a Admin. Estos grupos solo se crean en la
        # base local sanitizada mediante este comando explicito.
        user.groups.add(operations_group, catalog_group)

        locations = {}
        for external_id, name, reference in (
            ("local-baru", "Barú", "BARU"),
            ("local-compas", "Compas", "COMPAS_LOCAL_POR_CONFIRMAR"),
            ("local-taumm", "Taumm", "TAUMM"),
            ("local-proveedores", "Proveedores", "PROVEEDORES"),
            ("local-envia", "Bodega Envía", "ENVIA"),
        ):
            locations[name], _ = WarehouseLocation.objects.update_or_create(
                external_id=external_id,
                defaults={"name": name, "reference": reference, "active": True},
            )

        target_contacts = {
            "Barú": (
                ("Contacto Barú 1", "573000000001"),
                ("Contacto Barú 2", "573000000002"),
            ),
            "Compas": (("Contacto Compas local", "573000000003"),),
            "Taumm": (("Contacto Taumm local", "573000000004"),),
        }
        for warehouse_name, contacts in target_contacts.items():
            config, _ = MessagingConfig.objects.update_or_create(
                warehouse=locations[warehouse_name],
                defaults={"active": True, "updated_by": "seed_local_sanitized"},
            )
            for contact_name, phone in contacts:
                MessagingContact.objects.update_or_create(
                    config=config,
                    phone=phone,
                    defaults={"name": contact_name, "active": True},
                )
        for warehouse_name in ("Proveedores", "Bodega Envía"):
            MessagingConfig.objects.update_or_create(
                warehouse=locations[warehouse_name],
                defaults={"active": False, "updated_by": "seed_local_sanitized"},
            )

        now = timezone.now()
        examples = [
            {
                "channel": "shopify",
                "external_id": "local-shopify-19335",
                "visible_id": "19335",
                "total": "1139718",
                "customer": "Cliente multibodega",
                "items": [
                    ("8844", "Artículo Barú", 3, "200000"),
                    ("GV-L025", "Artículo Compas", 1, "339718"),
                    ("TAU-001", "Artículo Taumm", 2, "100000"),
                ],
                "shipments": [
                    ("local-19335-baru", "Barú", "", "", "without_guide", ["8844"]),
                    ("local-19335-compas", "Compas", "", "", "without_guide", ["GV-L025"]),
                    ("local-19335-taumm", "Taumm", "", "", "without_guide", ["TAU-001"]),
                ],
            },
            {
                "channel": "shopify",
                "external_id": "local-shopify-19346",
                "visible_id": "19346",
                "total": "834298",
                "customer": "Cliente sin guía",
                "items": [("SKU-19346", "Producto pendiente", 1, "834298")],
                "shipments": [("local-19346", "Barú", "", "Envia", "without_guide", ["SKU-19346"])],
            },
            {
                "channel": "shopify",
                "external_id": "local-shopify-19262",
                "visible_id": "19262",
                "total": "420000",
                "customer": "Cliente guía sin movimiento",
                "items": [("SKU-19262", "Producto con guía", 1, "420000")],
                "shipments": [("local-19262", "Barú", "ENV-19262", "Coordinadora", "guide_without_tracking", ["SKU-19262"])],
            },
            {
                "channel": "sodimac",
                "external_id": "local-sodimac-1001",
                "visible_id": "SOD-1001",
                "total": "139410",
                "customer": "Sodimac Colombia",
                "items": [("16165340", "Rejilla de prueba", 5, "27882")],
                "shipments": [("local-sodimac-1001", "Bodega Envía", "473462932", "Other", "in_transit", ["16165340"])],
            },
        ]

        for offset, example in enumerate(examples):
            order, _ = Order.objects.update_or_create(
                channel=example["channel"],
                external_id=example["external_id"],
                defaults={
                    "visible_id": example["visible_id"],
                    "placed_at": now - timezone.timedelta(hours=offset + 1),
                    "customer_name": example["customer"],
                    "currency": "COP",
                    "grand_total": Decimal(example["total"]),
                    "state": "open",
                    "source_snapshot": {"sanitized": True, "localFixture": True},
                },
            )
            item_map = {}
            for index, (sku, name, quantity, unit_price) in enumerate(example["items"]):
                item, _ = OrderItem.objects.update_or_create(
                    order=order,
                    external_id=f"{example['external_id']}-item-{index}",
                    defaults={
                        "sku": sku,
                        "name": name,
                        "quantity": quantity,
                        "unit_price": Decimal(unit_price),
                        "line_total": Decimal(unit_price) * quantity,
                    },
                )
                item_map[sku] = item
            desired_shipment_ids = {
                shipment_data[0] for shipment_data in example["shipments"]
            }
            # Solo limpia despachos obsoletos de esta fixture sanitizada. Nunca
            # toca pedidos reales ni registros que no provengan de local_fixture.
            Shipment.objects.filter(
                order=order,
                warehouse_assignment_source="local_fixture",
            ).exclude(external_id__in=desired_shipment_ids).delete()
            for external_id, warehouse_name, tracking, carrier, state, skus in example["shipments"]:
                shipment, _ = Shipment.objects.update_or_create(
                    order=order,
                    external_id=external_id,
                    defaults={
                        "warehouse": locations[warehouse_name],
                        "warehouse_name": warehouse_name,
                        "warehouse_reference": locations[warehouse_name].reference,
                        "warehouse_assignment_source": "local_fixture",
                        "carrier": carrier,
                        "tracking_number": tracking,
                        "tracking_source": "local_fixture" if tracking else "",
                        "logistics_state": state,
                    },
                )
                ShipmentItem.objects.filter(shipment=shipment).delete()
                for sku in skus:
                    ShipmentItem.objects.create(
                        shipment=shipment,
                        order_item=item_map[sku],
                        quantity=item_map[sku].quantity,
                    )
                if state == "in_transit":
                    TrackingEvent.objects.update_or_create(
                        shipment=shipment,
                        source="local_fixture",
                        external_event_id=f"{external_id}-event-1",
                        defaults={
                            "state_normalized": "in_transit",
                            "state_original": "in_transit",
                            "description": "Evento sanitizado para QA local",
                            "occurred_at": now - timezone.timedelta(minutes=30),
                        },
                    )

        for provider in ("shopify", "mercado_libre", "falabella", "sodimac", "envia"):
            IntegrationStatus.objects.update_or_create(
                provider=provider,
                defaults={
                    "state": "disabled_local",
                    "last_error_code": "",
                    "records_observed": 0,
                    "details": {"externalWrites": 0, "sanitized": True},
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Pedidos local listo: datos sanitizados, integraciones externas apagadas y externalWrites=0."
            )
        )
