from datetime import datetime, timedelta

from ..client import EnviaFulfillmentAPIError, EnviaFulfillmentClient

PAGE_SIZE = 50
MAX_PAGES = 40
# Holgura: una orden puede entrar a Envía un rato después de crearse en el
# canal (la tienda de Shopify la importa con retraso).
SINCE_MARGIN = timedelta(days=1)


def find_order(identifier, *, since):
    """Orden de Envía cuyo `identifier` es exactamente `identifier`, o None.

    Es lo que se consulta antes de crear una orden para no duplicarla, así
    que **nunca responde "no existe" sin haber buscado de verdad**.

    El filtro `search` del listado no filtra (probado el 2026-10-07: Envía
    devolvía las 12.361 órdenes con cualquier clave), y la consulta por id de
    Shopify (`/client/order/ecart/{id}`) responde 409. Se recorre entonces el
    listado, que viene de la más reciente a la más antigua, hasta pasar
    `since - SINCE_MARGIN` (`since`: cuándo se creó el pedido en el canal,
    `datetime` con zona). Si se agotan `MAX_PAGES` antes de llegar a esa
    fecha, falla con `ENVIA_FULFILLMENT_SEARCH_UNRELIABLE`.

    Ojo: las órdenes que la tienda de Shopify conectada importa sola llevan
    como identificador el número de Shopify (ej. `20399`).

    Devuelve {"order_id", "identifier", "warehouse_id", "warehouse_status",
    "fulfillment_status", "shop_name", "pretrackings"}.
    """
    identifier = str(identifier).strip()
    if not identifier:
        raise ValueError("identifier vacío")
    limit = since - SINCE_MARGIN
    client = EnviaFulfillmentClient()
    for page in range(1, MAX_PAGES + 1):
        data = client.get("/company/{company_id}/client/orders", params={"page": page, "limit": PAGE_SIZE}) or {}
        orders = data.get("orders") or []
        for order in orders:
            if str(order.get("identifier") or "").strip() == identifier:
                return {
                    "order_id": order.get("orderId"),
                    "identifier": order.get("identifier"),
                    "warehouse_id": order.get("warehouseId"),
                    "warehouse_status": order.get("warehouseStatus") or "",
                    "fulfillment_status": order.get("fulfillmentStatus") or "",
                    "shop_name": order.get("shopName") or "",
                    "pretrackings": order.get("pretrackings") or [],
                }
        if not orders or len(orders) < PAGE_SIZE:
            return None  # se recorrió todo el listado
        oldest = _parse(orders[-1].get("createdAt"))
        if oldest is not None and oldest < limit:
            return None
    raise EnviaFulfillmentAPIError(
        "ENVIA_FULFILLMENT_SEARCH_UNRELIABLE",
        provider_message=f"{MAX_PAGES} páginas sin llegar a {limit.isoformat()}",
    )


def _parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
