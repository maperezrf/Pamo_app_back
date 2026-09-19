from ..client import FalabellaClient
from ..constants import STATUS_MAP

VERSION = "2.0"


def get_orders(*, created_after, created_before, status=None, limit=100, offset=0):
    """Lista órdenes de Falabella por rango de fecha de creación, ya
    normalizadas.

    `created_after`/`created_before`: datetime con zona horaria.
    `status`: filtro opcional en el vocabulario propio de Falabella (ver
    STATUS_MAP en integrations/falabella/constants.py) -- no validado acá
    porque no se confirmó todavía la lista completa que acepta el filtro.

    Nombres de parámetro `CreatedAfter`/`CreatedBefore` confirmados contra
    la cuenta real el 2026-09-15 (devuelven pedidos correctamente).

    Devuelve una lista de:
        {"order_id", "order_number", "created_at", "updated_at",
         "status", "status_raw", "total", "customer_first_name",
         "customer_last_name", "customer_identification", "customer_email"}
    `created_at`/`updated_at` quedan como texto tal cual los manda
    Falabella (ej. "2026-09-09 15:31:39") -- no se confirmó su huso
    horario, así que no se convierten a datetime todavía.

    `customer_identification` es el número de cédula/NIT del comprador --
    viene en `NationalRegistrationNumber` a nivel de la orden (no dentro de
    `AddressBilling`), confirmado contra 5 pedidos reales el 2026-09-15
    (siempre presente, 8-10 dígitos). `customer_email` viene de
    `AddressBilling.CustomerEmail`. Si algún pedido no trae alguno de estos
    campos, se devuelve `""` -- decidir qué hacer con un pedido incompleto
    es responsabilidad de quien orquesta la importación, no de esta función.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    if offset < 0:
        raise ValueError("offset no puede ser negativo")
    parameters = {
        "CreatedAfter": created_after.isoformat(),
        "CreatedBefore": created_before.isoformat(),
        "Limit": limit,
        "Offset": offset,
    }
    if status:
        parameters["Status"] = status

    response = FalabellaClient().request("GetOrders", VERSION, parameters)
    return [_normalize_order(raw) for raw in _extract_orders(response)]


def _extract_orders(response):
    # Verificado contra una respuesta real el 2026-09-11.
    orders = (response.get("SuccessResponse") or {}).get("Body", {}).get("Orders", {})
    # Cuando no hay pedidos en el rango, Falabella manda "Orders": "" (string
    # vacío), no un objeto -- confirmado contra una respuesta real el
    # 2026-09-19 (rango sin pedidos). Sin este chequeo, `.get("Order", [])`
    # revienta con AttributeError porque un string no tiene `.get()`.
    if not isinstance(orders, dict):
        return []
    orders = orders.get("Order", [])
    # Con una sola orden, Falabella devuelve un diccionario suelto en vez
    # de una lista de uno.
    return [orders] if isinstance(orders, dict) else orders


def _normalize_order(raw):
    status_raw = (raw.get("Statuses") or {}).get("Status", "")
    billing = raw.get("AddressBilling") or {}
    return {
        "order_id": raw["OrderId"],
        "order_number": raw.get("OrderNumber", ""),
        "created_at": raw.get("CreatedAt", ""),
        "updated_at": raw.get("UpdatedAt", ""),
        "status": STATUS_MAP.get(status_raw.lower(), "requires_attention"),
        "status_raw": status_raw,
        "total": (raw.get("GrandTotal") or "").replace(",", ""),
        "customer_first_name": raw.get("CustomerFirstName", ""),
        "customer_last_name": raw.get("CustomerLastName", ""),
        "customer_identification": raw.get("NationalRegistrationNumber", ""),
        "customer_email": billing.get("CustomerEmail", ""),
    }
