from ..client import SiigoClient


def normalize_customer(raw):
    """Punto único de normalización de un cliente de Siigo -- lo usan
    get_customer y search_customer_by_identification para no duplicar
    esta forma en dos lados."""
    return {
        "id": raw["id"],
        "document_type": raw["id_type"]["name"],
        "identification": raw["identification"],
        "check_digit": raw.get("check_digit", ""),
        "name": " ".join(raw.get("name") or []).title(),
        "email": (raw.get("contacts") or [{}])[0].get("email", ""),
    }


def get_customer(customer_id):
    """Trae un cliente de Siigo por su id interno (el uuid que asigna
    Siigo, no la identificación/cédula/NIT)."""
    raw = SiigoClient().request("GET", f"customers/{customer_id}")
    return normalize_customer(raw)
