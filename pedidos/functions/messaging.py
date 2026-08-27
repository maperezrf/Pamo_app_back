from collections import defaultdict

from django.db import transaction

from pedidos.models import ManualFollowup, MessagingConfig, Shipment

from .serializers import whatsapp_url


def render_message(template, contact_name, warehouse_name, shipments):
    lines = []
    for shipment in shipments:
        item_lines = [
            f"SKU {item.order_item.sku or 'Sin SKU'} × {item.quantity}"
            for item in shipment.shipment_items.all()
        ]
        guide = shipment.tracking_number or "Sin guía"
        lines.append(
            "\n".join(
                [
                    f"Pedido {shipment.order.visible_id}",
                    *item_lines,
                    f"Guía: {guide}",
                ]
            )
        )
    replacements = {
        "{{contacto}}": contact_name,
        "{{bodega}}": warehouse_name,
        "{{cantidad_pedidos}}": str(len({shipment.order_id for shipment in shipments})),
        "{{lista_pedidos}}": "\n\n".join(lines),
        "{{numero_pedido}}": shipments[0].order.visible_id if shipments else "",
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


@transaction.atomic
def prepare_manual_followups(*, shipment_ids, actor):
    shipments = list(
        Shipment.objects.filter(id__in=shipment_ids)
        .select_related("order", "warehouse")
        .prefetch_related("shipment_items__order_item")
    )
    grouped = defaultdict(list)
    for shipment in shipments:
        if shipment.warehouse_id:
            grouped[shipment.warehouse_id].append(shipment)

    generated = []
    missing_config = []
    for warehouse_id, warehouse_shipments in grouped.items():
        config = (
            MessagingConfig.objects.filter(warehouse_id=warehouse_id, active=True)
            .prefetch_related("contacts")
            .first()
        )
        if not config:
            missing_config.append(warehouse_shipments[0].effective_warehouse_name)
            continue
        contacts = list(config.contacts.filter(active=True))
        if not contacts:
            missing_config.append(warehouse_shipments[0].effective_warehouse_name)
            continue
        for contact in contacts:
            rendered = render_message(
                config.template_body,
                contact.name,
                warehouse_shipments[0].effective_warehouse_name,
                warehouse_shipments,
            )
            followup = ManualFollowup.objects.create(
                warehouse_id=warehouse_id,
                contact_name=contact.name,
                phone=contact.phone,
                order_numbers=sorted({item.order.visible_id for item in warehouse_shipments}),
                rendered_message=rendered,
                prepared_by=actor,
            )
            generated.append(
                {
                    "id": str(followup.id),
                    "warehouse": followup.warehouse.name,
                    "contactName": followup.contact_name,
                    "phone": followup.phone,
                    "orderNumbers": followup.order_numbers,
                    "rendered_message": followup.rendered_message,
                    "state": followup.state,
                    "whatsappUrl": whatsapp_url(followup.phone, followup.rendered_message),
                    "created_at": followup.created_at.isoformat(),
                }
            )
    return generated, missing_config

