from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from config.constants import SODIMAC_CUTOVER_DATE, SODIMAC_LEGACY_OCS, SODIMAC_SHOPIFY_CUSTOMER_ID
from integrations.pamo_web.functions.run_sodimac_invoicing import run_sodimac_invoicing
from integrations.sodimac.functions.get_orders import get_orders
from integrations.sodimac.functions.reinject_order import reinject_order

from ..models import MarketplaceOrder, MarketplaceOrderItem
from .claim_orders import RETRYABLE_STATUSES, claim_orders
from .process_shipment import process_shipment

SODIMAC = MarketplaceOrder.Marketplace.SODIMAC
FINAL_STATUS = "4-ESTADO FINAL"
ORDER_TYPES = ("1", "4")
# Sodimac paga a crédito: la orden no queda pagada en Shopify (como en pamo_web).
FINANCIAL_STATUS = "PENDING"
# Formatos vistos/posibles de FECHA_TRANSMISION; pamo_web la leía con
# dateutil y dayfirst=True.
DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y")


def sync_sodimac_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso registrado en orchestrator como `orders.sync_sodimac`. Ver
    docs/apps/orders.md ("Sodimac").

    1. Reinyecta las OC abiertas (estado distinto de "4-ESTADO FINAL"): es
       la única forma de conocer su estado actual.
    2. Lee la cola (tipos "1" y "4") y guarda cada OC apenas se lee, porque
       la cola es destructiva: lo leído y no guardado se pierde.
       - OC nueva: fila e ítems (precio = COSTO_SKU sin IVA).
       - OC conocida: actualiza `marketplace_status`.
       - OC de pamo_web (transmitida antes de SODIMAC_CUTOVER_DATE o en
         SODIMAC_LEGACY_OCS): no se guarda; se devuelve a la cola para que
         la lea pamo_web.
    3. Crea en Shopify las OC sin orden, con reclamo atómico. Un pedido =
       un envío.
    4. Llama a pamo_web para que facture sus OC antiguas, justo después
       (Parte C). Si falla, se informa; lo de aquí no se revierte.

    `params` opcional: `{"limit": N}` limita cuántas OC se crean en Shopify
    (no limita la lectura de la cola).

    Un fallo puntual no detiene el resto; al final, si hubo fallos, la
    ejecución termina en error con el resumen.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    customer_id = SODIMAC_SHOPIFY_CUSTOMER_ID
    if not customer_id:
        raise RuntimeError("SODIMAC_SHOPIFY_CUSTOMER_ID no está configurado")
    cutover = _cutover_date()
    legacy_ocs = {str(oc).strip() for oc in SODIMAC_LEGACY_OCS if str(oc).strip()}
    failures = []

    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    progress_callback(5, "Reinyectando OC abiertas")
    reinjected = _reinject_open_orders(failures)

    # Entre leer y guardar no hay checkpoints de cancelación: lo leído se
    # perdería.
    progress_callback(20, "Leyendo la cola de Sodimac")
    counts = {"nuevas": 0, "actualizadas": 0, "de_pamo_web": 0}
    for order_type in ORDER_TYPES:
        try:
            rows = get_orders(order_type)
        except Exception as error:  # un tipo no detiene el otro
            failures.append(f"lectura tipo {order_type} ({type(error).__name__}: {error})")
            continue
        for purchase_order, oc_rows in _group_by_purchase_order(rows).items():
            _save_purchase_order(purchase_order, oc_rows, cutover, legacy_ocs, counts, failures)

    progress_callback(50, "Creando órdenes en Shopify")
    created = _create_shopify_orders(customer_id, params.get("limit"), failures, progress_callback, cancellation_token)

    progress_callback(90, "Facturación de pamo_web")
    try:
        result = run_sodimac_invoicing()
    except Exception as error:
        pamo_web = "falló"
        failures.append(f"pamo_web ({type(error).__name__}: {error})")
    else:
        pamo_web = {
            key: len(result.get(key) or [])
            for key in ("read", "returned_to_queue", "not_returned", "invoiced", "errors")
        }
        # OC nuevas que leyó pamo_web y no pudo devolver a la cola: aquí no
        # llegarán solas. Revisarlas a mano (reinyectarlas desde Sodimac).
        if result.get("not_returned"):
            failures.append(f"pamo_web no devolvió a la cola: {', '.join(map(str, result['not_returned']))}")

    summary = (
        f"reinyectadas {reinjected}; cola: {counts}; creadas en Shopify {created}; pamo_web: {pamo_web}"
    )
    if failures:
        raise RuntimeError(f"{summary}; fallaron {len(failures)}: {'; '.join(failures)}")
    progress_callback(100, summary)


def _cutover_date():
    try:
        return date.fromisoformat(SODIMAC_CUTOVER_DATE)
    except ValueError as error:
        raise RuntimeError("SODIMAC_CUTOVER_DATE no está configurado (AAAA-MM-DD)") from error


def _reinject_open_orders(failures):
    open_ocs = (
        MarketplaceOrder.objects.filter(marketplace=SODIMAC)
        .exclude(marketplace_status=FINAL_STATUS)
        .order_by("created_at")
        .values_list("marketplace_order_id", flat=True)
    )
    reinjected = 0
    for purchase_order in open_ocs:
        try:
            reinject_order(purchase_order)
            reinjected += 1
        except Exception as error:  # una OC no detiene el resto
            failures.append(f"reinyección {purchase_order} ({type(error).__name__}: {error})")
    return reinjected


def _group_by_purchase_order(rows):
    grouped = OrderedDict()
    for row in rows:
        grouped.setdefault(str(row["purchase_order"]).strip(), []).append(row)
    return grouped


def _save_purchase_order(purchase_order, rows, cutover, legacy_ocs, counts, failures):
    transmitted_at = _parse_transmitted_at(rows[0]["transmitted_at"])
    if transmitted_at is None:
        # Sin fecha no se sabe si es de pamo_web: no se guarda y vuelve a la
        # cola para no perderla.
        failures.append(f"{purchase_order}: FECHA_TRANSMISION no reconocida {rows[0]['transmitted_at']!r}")
        _give_back(purchase_order, failures)
        return
    if transmitted_at.date() < cutover or purchase_order in legacy_ocs:
        counts["de_pamo_web"] += 1
        _give_back(purchase_order, failures)
        return

    status = str(rows[0]["status"] or "").strip()
    order = MarketplaceOrder.objects.filter(marketplace=SODIMAC, marketplace_order_id=purchase_order).first()
    if order is None:
        try:
            with transaction.atomic():
                order = MarketplaceOrder.objects.create(
                    marketplace=SODIMAC,
                    marketplace_order_id=purchase_order,
                    marketplace_order_number=purchase_order,
                    marketplace_status=status,
                    marketplace_created_at=transmitted_at,
                )
                _create_items(order, rows)
        except IntegrityError:
            order = MarketplaceOrder.objects.get(marketplace=SODIMAC, marketplace_order_id=purchase_order)
        else:
            counts["nuevas"] += 1
            return

    # Solo el estado del marketplace: un `save()` completo podría pisar el
    # reclamo de otro proceso (docs/apps/orders.md).
    MarketplaceOrder.objects.filter(pk=order.pk).update(marketplace_status=status, updated_at=timezone.now())
    if not order.items.exists():
        _create_items(order, rows)
    counts["actualizadas"] += 1


def _create_items(order, rows):
    MarketplaceOrderItem.objects.bulk_create(
        MarketplaceOrderItem(
            order=order,
            marketplace_sku=row["sku"],
            quantity=int(Decimal(str(row["quantity"]))),
            unit_price=str(row["cost"]),
        )
        for row in rows
    )


def _give_back(purchase_order, failures):
    try:
        reinject_order(purchase_order)
    except Exception as error:
        failures.append(f"devolver a la cola {purchase_order} ({type(error).__name__}: {error})")


def _parse_transmitted_at(value):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for date_format in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _create_shopify_orders(customer_id, limit, failures, progress_callback, cancellation_token):
    pending = list(
        MarketplaceOrder.objects.filter(marketplace=SODIMAC, shopify_order_id="", status__in=RETRYABLE_STATUSES)
        .order_by("created_at")
        .values_list("marketplace_order_id", flat=True)
    )
    if limit:
        pending = pending[: int(limit)]
    created = 0
    total = len(pending) or 1
    for index, purchase_order in enumerate(pending):
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        claimed = claim_orders(SODIMAC, [purchase_order])
        if claimed:
            try:
                process_shipment(claimed, customer_id, financial_status=FINANCIAL_STATUS)
            except Exception as error:  # queda en `procesando`: revisión manual
                failures.append(f"Shopify {purchase_order} ({type(error).__name__}: {error})")
            else:
                claimed[0].refresh_from_db(fields=["status", "error_description"])
                if claimed[0].status == MarketplaceOrder.Status.CREATED:
                    created += 1
                else:
                    failures.append(f"Shopify {purchase_order}: {claimed[0].error_description}")
        progress_callback(50 + int(40 * (index + 1) / total), f"OC {purchase_order}")
    return created
