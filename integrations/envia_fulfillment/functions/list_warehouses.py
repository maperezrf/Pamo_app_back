from ..client import EnviaFulfillmentClient


def list_warehouses():
    """Bodegas de la empresa (`GET /company/{companyId}/warehouse/client`).

    Devuelve [{"id": int, "name": str, "city": str}], sin la dirección
    completa.
    """
    rows = EnviaFulfillmentClient().get("/company/{company_id}/warehouse/client") or []
    return [
        {"id": row.get("id"), "name": row.get("name") or "", "city": (row.get("address") or {}).get("city") or ""}
        for row in rows
        if isinstance(row, dict)
    ]
