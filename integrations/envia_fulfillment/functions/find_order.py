from ..client import EnviaFulfillmentAPIError, EnviaFulfillmentClient

PAGE_SIZE = 50


def find_order(identifier):
    """Orden de Envía cuyo `identifier` es exactamente `identifier`, o None.

    Es lo que se consulta antes de crear una orden para no duplicarla, así
    que **nunca responde "no existe" sin haber buscado de verdad**.

    POR VERIFICAR: el listado (`GET /company/{companyId}/client/orders`)
    exige que `search` sea un objeto, pero ninguna clave probada el
    2026-10-07 (`identifier`, `name`, `number`, `orderNumber`, `query`...)
    filtró: Envía devolvía el total sin filtrar (12.361). Se manda
    `search[identifier]`; si la respuesta trae más de una página y ninguna
    coincide, el filtro no se aplicó y se lanza
    `ENVIA_FULFILLMENT_SEARCH_UNRELIABLE` en vez de devolver None.

    Devuelve {"order_id", "identifier", "warehouse_id", "warehouse_status",
    "fulfillment_status"}.
    """
    identifier = str(identifier).strip()
    if not identifier:
        raise ValueError("identifier vacío")
    page = EnviaFulfillmentClient().get(
        "/company/{company_id}/client/orders",
        params={"page": 1, "limit": PAGE_SIZE, "search[identifier]": identifier},
    ) or {}
    orders = page.get("orders") or []
    for order in orders:
        if str(order.get("identifier") or "").strip() == identifier:
            return {
                "order_id": order.get("orderId"),
                "identifier": order.get("identifier"),
                "warehouse_id": order.get("warehouseId"),
                "warehouse_status": order.get("warehouseStatus") or "",
                "fulfillment_status": order.get("fulfillmentStatus") or "",
            }
    if int(page.get("totalRows") or len(orders)) > len(orders):
        raise EnviaFulfillmentAPIError(
            "ENVIA_FULFILLMENT_SEARCH_UNRELIABLE",
            provider_message=f"{page.get('totalRows')} resultados sin coincidencia exacta: el filtro no se aplicó",
        )
    return None
