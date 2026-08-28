import base64
import hashlib
import hmac
import json
import time

from django.conf import settings


class InteractivePayloadError(Exception):
    pass


def _encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def signed_action(*, shipment_id, contact_reference, action, issued_at=None):
    payload = {
        "v": 1,
        "s": str(shipment_id),
        "c": str(contact_reference),
        "a": str(action),
        "iat": int(issued_at or time.time()),
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(
        settings.SECRET_KEY.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()[:24]
    return f"p1.{encoded}.{signature}"


def parse_signed_action(value, *, maximum_age_seconds=7 * 24 * 60 * 60):
    try:
        prefix, encoded, signature = str(value or "").split(".", 2)
        expected = hmac.new(
            settings.SECRET_KEY.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()[:24]
        if prefix != "p1" or not hmac.compare_digest(signature, expected):
            raise InteractivePayloadError("interactive_payload_invalid")
        payload = json.loads(_decode(encoded).decode("utf-8"))
        issued_at = int(payload.get("iat") or 0)
        if issued_at <= 0 or abs(int(time.time()) - issued_at) > maximum_age_seconds:
            raise InteractivePayloadError("interactive_payload_expired")
        if not all(payload.get(key) for key in ("s", "c", "a")):
            raise InteractivePayloadError("interactive_payload_incomplete")
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        if isinstance(error, InteractivePayloadError):
            raise
        raise InteractivePayloadError("interactive_payload_invalid") from error


def supplier_order_interactive(*, shipment_id, contact_reference):
    return {
        "type": "list",
        "body": "Selecciona una opción para este despacho:",
        "button": "Responder",
        "sections": [
            {
                "title": "Estado del despacho",
                "rows": [
                    {
                        "id": signed_action(
                            shipment_id=shipment_id,
                            contact_reference=contact_reference,
                            action=action,
                        ),
                        "title": title,
                    }
                    for action, title in (
                        ("order_received", "Confirmado"),
                        ("report_stockout", "Agotado"),
                        ("request_guide", "Listo para despacho"),
                        ("report_issue", "Reportar novedad"),
                    )
                ],
            }
        ],
    }


def issue_sku_interactive(*, shipment, contact_reference):
    items = list(shipment.shipment_items.select_related("order_item").all())
    if len(items) > 10:
        raise InteractivePayloadError("stockout_item_limit_exceeded")
    return {
        "type": "list",
        "body": "Selecciona únicamente la referencia afectada:",
        "button": "Seleccionar SKU",
        "sections": [
            {
                "title": "Referencias del despacho",
                "rows": [
                    {
                        "id": signed_action(
                            shipment_id=shipment.id,
                            contact_reference=contact_reference,
                            action=f"issue_item:{item.id}",
                        ),
                        "title": (item.order_item.sku or "Sin SKU")[:24],
                    }
                    for item in items
                ],
            }
        ],
    }


def novelty_menu_interactive(*, shipment_id, contact_reference):
    choices = [
        ("supplier_stockout", "Producto agotado"),
        ("supplier_partial", "Cantidad incompleta"),
        ("supplier_damage", "Producto averiado"),
        ("supplier_guide_issue", "Problema con la guía"),
        ("supplier_delay", "Retraso / no despacha"),
        ("supplier_other", "Otra novedad"),
    ]
    return {
        "type": "list",
        "body": "Indica el tipo de novedad:",
        "button": "Seleccionar novedad",
        "sections": [
            {
                "title": "Novedades",
                "rows": [
                    {
                        "id": signed_action(
                            shipment_id=shipment_id,
                            contact_reference=contact_reference,
                            action=action,
                        ),
                        "title": title,
                    }
                    for action, title in choices
                ],
            }
        ],
    }
