from urllib.parse import quote


def decimal_value(value):
    return None if value is None else str(value)


def shipment_dict(shipment, *, detailed=False):
    data = {
        "id": str(shipment.id),
        "order_id": str(shipment.order_id),
        "warehouse_location_id": str(shipment.warehouse_id) if shipment.warehouse_id else None,
        "warehouse_name": shipment.effective_warehouse_name or None,
        "warehouse_reference": shipment.warehouse_reference or None,
        "warehouse_locked": shipment.warehouse_locked,
        "warehouse_assignment_source": shipment.warehouse_assignment_source,
        "carrier": shipment.carrier or None,
        "tracking_number": shipment.tracking_number or None,
        "tracking_url": shipment.tracking_url or None,
        "tracking_source": shipment.tracking_source or None,
        "logistics_state": shipment.logistics_state,
        "carrier_state_original": shipment.carrier_state_original or None,
        "carrier_cost": decimal_value(shipment.carrier_cost),
        "carrier_cost_currency": shipment.carrier_cost_currency or None,
        "carrier_cost_source": shipment.carrier_cost_source or None,
        "incident_category": shipment.incident_category or None,
        "incident_detail": shipment.incident_detail or None,
        "customer_context": shipment.customer_context or None,
        "messaging_state": shipment.messaging_state,
        "version": shipment.version,
        "has_document": hasattr(shipment, "document"),
        "document_url": f"/api/pedidos/shipments/{shipment.id}/document/" if hasattr(shipment, "document") else None,
    }
    if detailed:
        data["items"] = [
            {
                "order_item_id": str(item.order_item_id),
                "sku": item.order_item.sku,
                "name": item.order_item.name,
                "quantity": item.quantity,
                "order_quantity": item.order_item.quantity,
            }
            for item in shipment.shipment_items.all()
        ]
        data["tracking_events"] = [
            {
                "id": str(event.id),
                "state_normalized": event.state_normalized,
                "state_original": event.state_original or None,
                "description": event.description or None,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in shipment.tracking_events.all()
        ]
    return data


def order_row(order):
    shipments = list(order.shipments.all())
    warehouse_names = [item.effective_warehouse_name or "Sin asignar" for item in shipments]
    carriers = [item.carrier or "Pendiente" for item in shipments]
    tracking_numbers = [item.tracking_number or "Sin guía" for item in shipments]
    logistics_states = [item.logistics_state for item in shipments]
    incident_categories = [item.incident_category for item in shipments if item.incident_category]
    total_cost = sum((item.carrier_cost or 0) for item in shipments)
    source_snapshot = order.source_snapshot if isinstance(order.source_snapshot, dict) else {}
    business_origin = source_snapshot.get("business_origin") or order.channel
    business_origin_via = source_snapshot.get("business_origin_via") or order.channel
    return {
        "id": str(order.id),
        "row_id": str(order.id),
        "channel_order_id": order.visible_id,
        "order_number": order.visible_id,
        "channel": order.channel,
        "business_origin": business_origin,
        "business_origin_via": business_origin_via,
        "business_origin_confidence": source_snapshot.get("business_origin_confidence"),
        "external_id": order.external_id,
        "channel_order_url": order.source_url or None,
        "placed_at": order.placed_at.isoformat(),
        "customer_name": order.customer_name or "Cliente sin nombre",
        "customer_email": order.customer_email or None,
        "currency": order.currency,
        "grand_total": str(order.grand_total),
        "item_count": sum(item.quantity for item in order.items.all()),
        "shipment_count": len(shipments),
        "shipment_ids": [str(item.id) for item in shipments],
        "warehouses": warehouse_names,
        "carriers": carriers,
        "tracking_numbers": tracking_numbers,
        "carrier_cost": str(total_cost) if total_cost else None,
        "logistics_state": logistics_states,
        "incident_category": incident_categories,
        "messaging_state": [item.messaging_state for item in shipments],
        "state": order.state,
        "version": max((item.version for item in shipments), default=1),
    }


def order_detail(order):
    data = order_row(order)
    data.update(
        {
            "customer_phone": order.customer_phone or None,
            "source_snapshot": order.source_snapshot,
            "items": [
                {
                    "id": str(item.id),
                    "sku": item.sku,
                    "name": item.name,
                    "quantity": item.quantity,
                    "unit_price": str(item.unit_price),
                    "line_total": str(item.line_total),
                }
                for item in order.items.all()
            ],
            "shipments": [shipment_dict(item, detailed=True) for item in order.shipments.all()],
            "logistics_audit": [
                {
                    "id": event.id,
                    "shipment_id": str(event.shipment_id),
                    "field": event.field,
                    "actor": event.actor,
                    "source": event.source,
                    "detail": event.detail,
                    "created_at": event.created_at.isoformat(),
                }
                for event in LogisticsAudit.objects.filter(shipment__order=order).select_related("shipment")[:100]
            ],
        }
    )
    return data


def whatsapp_url(phone, message):
    digits = "".join(character for character in phone if character.isdigit())
    return f"https://wa.me/{digits}?text={quote(message)}"


from pedidos.models import LogisticsAudit  # noqa: E402
