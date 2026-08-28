import hashlib

from django.db import transaction

from pedidos.models import ManualFollowup, MessagingConfig, Shipment

from .serializers import whatsapp_url


def render_message(template, contact_name, warehouse_name, shipment):
    item_lines = [
        f"SKU {item.order_item.sku or 'Sin SKU'} - {item.order_item.name} x {item.quantity}"
        for item in shipment.shipment_items.all()
    ]
    guide = shipment.tracking_number or "Sin guía"
    dispatch = "\n".join(
        [f"Pedido {shipment.order.visible_id}", *item_lines, f"Guía: {guide}"]
    )
    replacements = {
        "{{contacto}}": contact_name,
        "{{bodega}}": warehouse_name,
        "{{cantidad_pedidos}}": "1",
        "{{lista_pedidos}}": dispatch,
        "{{numero_pedido}}": shipment.order.visible_id,
        "{{sku_lineas}}": "\n".join(item_lines),
        "{{guia}}": guide,
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


def _preparation_key(config, contact, shipment, rendered):
    material = "|".join(
        [
            str(config.id),
            config.updated_at.isoformat(),
            str(contact.id),
            str(shipment.id),
            str(shipment.version),
            rendered,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _serialize_followup(followup, *, replayed=False):
    return {
        "id": str(followup.id),
        "shipmentId": str(followup.shipment_id) if followup.shipment_id else None,
        "contactId": followup.contact_id,
        "warehouse": followup.warehouse.name if followup.warehouse_id else "Sin asignar",
        "contactName": followup.contact_name,
        "phone": followup.phone,
        "orderNumbers": followup.order_numbers,
        "rendered_message": followup.rendered_message,
        "state": followup.state,
        "replayed": replayed,
        "whatsappUrl": whatsapp_url(followup.phone, followup.rendered_message),
        "created_at": followup.created_at.isoformat(),
    }


@transaction.atomic
def prepare_manual_followups(*, shipment_ids, actor):
    shipments = list(
        Shipment.objects.filter(id__in=shipment_ids)
        .select_related("order", "warehouse")
        .prefetch_related("shipment_items__order_item")
        .order_by("created_at", "id")
    )
    generated = []
    skipped = []
    for shipment in shipments:
        warehouse_name = shipment.effective_warehouse_name or "Sin asignar"
        if not shipment.warehouse_id:
            skipped.append(
                {
                    "shipmentId": str(shipment.id),
                    "warehouse": warehouse_name,
                    "reason": "warehouse_not_assigned",
                }
            )
            continue
        config = (
            MessagingConfig.objects.filter(warehouse_id=shipment.warehouse_id)
            .prefetch_related("contacts")
            .first()
        )
        if not config or not config.active:
            skipped.append(
                {
                    "shipmentId": str(shipment.id),
                    "warehouse": warehouse_name,
                    "reason": "warehouse_messaging_disabled",
                }
            )
            continue
        contacts = list(config.contacts.filter(active=True).order_by("id"))
        if not contacts:
            skipped.append(
                {
                    "shipmentId": str(shipment.id),
                    "warehouse": warehouse_name,
                    "reason": "active_contact_missing",
                }
            )
            continue
        for contact in contacts:
            rendered = render_message(
                config.template_body, contact.name, warehouse_name, shipment
            )
            key = _preparation_key(config, contact, shipment, rendered)
            followup, created = ManualFollowup.objects.get_or_create(
                preparation_key=key,
                defaults={
                    "warehouse_id": shipment.warehouse_id,
                    "shipment": shipment,
                    "contact": contact,
                    "contact_name": contact.name,
                    "phone": contact.phone,
                    "order_numbers": [shipment.order.visible_id],
                    "rendered_message": rendered,
                    "prepared_by": actor,
                },
            )
            generated.append(_serialize_followup(followup, replayed=not created))
    missing_config = sorted({item["warehouse"] for item in skipped})
    return generated, missing_config, skipped
