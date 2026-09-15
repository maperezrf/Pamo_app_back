from ..client import SiigoClient
from .get_customer import normalize_customer


def search_customer_by_identification(identification):
    """Busca un cliente de Siigo por su número de identificación (cédula/
    NIT) -- reemplaza la sincronización masiva de clientes del proyecto
    anterior: se consulta en vivo, no se cachea localmente.

    Filtro `identification=` verificado contra la cuenta real el
    2026-09-11 -- devuelve como máximo un resultado (Siigo no permite
    identificaciones duplicadas).

    Devuelve el cliente normalizado, o `None` si no existe.
    """
    response = SiigoClient().request(
        "GET", "customers", params={"identification": str(identification)}
    )
    results = response.get("results") or []
    if not results:
        return None
    return normalize_customer(results[0])
