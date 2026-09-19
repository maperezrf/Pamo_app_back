from customers.functions.reconcile_from_shopify import reconcile_from_shopify

from .fetch_falabella_orders import fetch_falabella_orders
from .process_pending_orders import process_pending_orders


def import_falabella_orders(params=None, progress_callback=None, cancellation_token=None):
    """Proceso registrado en orchestrator como `orders.import_falabella`.
    Tres etapas, en orden: refrescar el directorio de clientes, descubrir
    pedidos nuevos de Falabella, y procesar todo lo pendiente (nuevo o en
    error) hacia Shopify. Contrato de proceso:
    `(params, progress_callback=None, cancellation_token=None)`.

    `params` opcional:
    - `"limit"`: limita cuántos pedidos pendientes se procesan hacia
      Shopify en esta corrida (pensado para una primera corrida controlada
      contra la cuenta real, ej. `{"limit": 1}`). No limita el
      descubrimiento de pedidos nuevos, solo el paso que escribe en
      Shopify.
    - `"created_after"`: desde cuándo traer pedidos de Falabella (string
      ISO-8601, ej. `{"created_after": "2026-09-22T00:00:00+00:00"}` para
      "desde hoy en adelante"). Sin esto, trae las últimas 48h por
      defecto -- ver `fetch_falabella_orders`.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)

    progress_callback(0, "Reconciliando directorio de clientes")
    reconcile_from_shopify(
        progress_callback=lambda percent, step=None: progress_callback(int(percent * 0.3), step),
        cancellation_token=cancellation_token,
    )

    progress_callback(30, "Trayendo pedidos nuevos de Falabella")
    fetch_falabella_orders(
        progress_callback=lambda percent, step=None: progress_callback(30 + int(percent * 0.2), step),
        cancellation_token=cancellation_token,
        created_after=params.get("created_after"),
    )

    progress_callback(50, "Procesando pedidos pendientes")
    process_pending_orders(
        progress_callback=lambda percent, step=None: progress_callback(50 + int(percent * 0.5), step),
        cancellation_token=cancellation_token,
        limit=params.get("limit"),
    )

    progress_callback(100, "Completado")
