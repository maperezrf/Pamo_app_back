import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from integrations.siigo.client import SiigoAPIError
from integrations.siigo.functions.create_invoice import create_invoice
from orders.models import MarketplaceOrder

from ..constants import (
    SODIMAC_COST_CENTER,
    SODIMAC_CUSTOMER,
    SODIMAC_CUSTOMER_NAME,
    SODIMAC_DOCUMENT_ID,
    SODIMAC_MAIL_SEND,
    SODIMAC_PAYMENT_ID,
    SODIMAC_RETENTION_IDS,
    SODIMAC_SELLER,
    SODIMAC_STAMP_SEND,
)
from ..models import Invoice
from .build_sodimac_invoice import build_sodimac_invoice

FINAL_STATUS = "4-ESTADO FINAL"
CALCULATED_TOTAL_PATTERN = re.compile(r"The total invoice calculated is\s*([0-9.,]+)")


def invoice_sodimac_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso registrado en orchestrator como `invoicing.invoice_sodimac`:
    factura en Siigo las OC de Sodimac en "4-ESTADO FINAL" sin factura, o
    con factura en `error`. Se factura aunque la orden de Shopify haya
    fallado, como en pamo_web. Ver docs/apps/invoicing.md.

    Por cada OC: calcula, guarda la factura en `creando` y llama a Siigo.
    - Éxito: `creada`, con id, número y total de Siigo.
    - Rechazo de Siigo (`SiigoAPIError`): `error`; si Siigo informa el total
      que calculó, se guarda y el reintento lo envía como pago.
    - Cualquier otra excepción: queda en `creando` (no se sabe si la
      factura existe) y se revisa a mano.

    `params` opcional: `{"limit": N}` máximo de OC por corrida.
    Al final, si hubo fallos, la ejecución termina en error con el resumen.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    limit = params.get("limit")

    orders = list(
        MarketplaceOrder.objects.filter(marketplace=MarketplaceOrder.Marketplace.SODIMAC, marketplace_status=FINAL_STATUS)
        .filter(Q(invoice__isnull=True) | Q(invoice__status=Invoice.Status.ERROR))
        .order_by("created_at")
    )
    if limit:
        orders = orders[: int(limit)]
    progress_callback(10, f"{len(orders)} OC por facturar")

    created = 0
    failures = []
    total = len(orders) or 1
    for index, order in enumerate(orders):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        outcome = _invoice_order(order)
        if outcome == Invoice.Status.CREATED:
            created += 1
        elif outcome:
            failures.append(f"{order.marketplace_order_id}: {outcome}")
        progress_callback(10 + int(85 * (index + 1) / total), f"OC {order.marketplace_order_id}")

    summary = f"{len(orders)} OC revisadas, {created} facturas creadas"
    if failures:
        raise RuntimeError(f"{summary}; fallaron {len(failures)}: {'; '.join(failures)}")
    progress_callback(100, summary)


def _invoice_order(order):
    """Factura una OC. Devuelve `Invoice.Status.CREATED`, el mensaje del
    fallo, o "" si otro proceso la tomó."""
    previous = Invoice.objects.filter(marketplace_order=order).first()
    calculated_total = previous.siigo_calculated_total if previous else None
    built = build_sodimac_invoice(order, calculated_total=calculated_total)
    invoice_date = timezone.localdate()
    request = {
        "document_id": SODIMAC_DOCUMENT_ID,
        "customer": SODIMAC_CUSTOMER,
        "items": built["items"],
        "cost_center": SODIMAC_COST_CENTER,
        "seller": SODIMAC_SELLER,
        "retention_ids": SODIMAC_RETENTION_IDS,
        "reteica": float(built["reteica"]),
        "payment_id": SODIMAC_PAYMENT_ID,
        "payment_value": float(built["total"]),
        "stamp_send": SODIMAC_STAMP_SEND,
        "mail_send": SODIMAC_MAIL_SEND,
        "date": invoice_date.isoformat(),
        "purchase_order_number": order.marketplace_order_id,
    }
    fields = {
        "status": Invoice.Status.CREATING,
        "customer_identification": SODIMAC_CUSTOMER["identification"],
        "customer_name": SODIMAC_CUSTOMER_NAME,
        "purchase_order_number": order.marketplace_order_id,
        "subtotal": built["subtotal"],
        "iva": built["iva"],
        "reteiva": built["reteiva"],
        "reteica": built["reteica"],
        "retefuente": built["retefuente"],
        "total": built["total"],
        "stamped": SODIMAC_STAMP_SEND,
        "invoice_date": invoice_date,
        "error_message": "",
        "request_payload": request,
        "response_payload": {},
    }

    # `creando` queda escrito ANTES de llamar a Siigo, y solo si nadie más
    # tomó la OC: nueva por el OneToOne, reintento solo desde `error`.
    if previous is None:
        try:
            invoice = Invoice.objects.create(marketplace_order=order, **fields)
        except IntegrityError:
            return ""
    else:
        if not Invoice.objects.filter(pk=previous.pk, status=Invoice.Status.ERROR).update(
            updated_at=timezone.now(), **fields
        ):
            return ""
        invoice = Invoice.objects.get(pk=previous.pk)

    try:
        result = create_invoice(**request)
    except SiigoAPIError as error:
        invoice.status = Invoice.Status.ERROR
        invoice.error_message = _siigo_message(error.body)
        invoice.response_payload = error.body if isinstance(error.body, dict) else {"body": str(error.body)}
        invoice.siigo_calculated_total = _calculated_total(invoice.error_message)
        invoice.save(
            update_fields=["status", "error_message", "response_payload", "siigo_calculated_total", "updated_at"]
        )
        return invoice.error_message
    except Exception as error:  # incierto: queda en `creando`
        invoice.error_message = f"{type(error).__name__}: {error}"
        invoice.save(update_fields=["error_message", "updated_at"])
        return f"resultado incierto, revisar en Siigo ({invoice.error_message})"

    raw = result["raw"]
    invoice.status = Invoice.Status.CREATED
    invoice.siigo_id = str(result["invoice_id"])
    invoice.number = str(raw.get("name") or "")
    invoice.siigo_total = _decimal(raw.get("total"))
    invoice.response_payload = raw
    invoice.save(update_fields=["status", "siigo_id", "number", "siigo_total", "response_payload", "updated_at"])
    return Invoice.Status.CREATED


def _siigo_message(body):
    # Forma de un rechazo de Siigo: {"Errors": [{"Message": "..."}]}.
    if isinstance(body, dict):
        messages = [str(error.get("Message", "")) for error in body.get("Errors") or [] if isinstance(error, dict)]
        if any(messages):
            return " | ".join(message for message in messages if message)
    return str(body)


def _calculated_total(message):
    match = CALCULATED_TOTAL_PATTERN.search(message or "")
    if not match:
        return None
    number = match.group(1).rstrip(".,")
    # pamo_web enviaba el texto tal cual; por si llega con separador de
    # miles ("1,234.56") o coma decimal ("1234,56").
    number = number.replace(",", "") if "." in number else number.replace(",", ".")
    return _decimal(number)


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
