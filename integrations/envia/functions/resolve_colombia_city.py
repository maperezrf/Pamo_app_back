import re
import unicodedata

from ..client import EnviaAPIError, EnviaClient


def _city_key(value):
    return " ".join(
        unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().upper().split()
    )


def resolve_colombia_city(city, shopify_state):
    """Traduce una ciudad/departamento de Shopify al código DANE que
    Envía exige para destinos en Colombia -- nunca envía calle, nombre ni
    teléfono, solo referencia geográfica.

    Devuelve {"city": código DANE de 8 dígitos, "state": código de 2
    dígitos del departamento}.
    """
    client = EnviaClient()
    states = client.get(client.queries_base, "/state", params={"country_code": "CO"}).get("data") or []
    codes = {
        row.get("code_2_digits")
        for row in states
        if isinstance(row, dict) and row.get("country_code") == "CO" and row.get("code_shopify") == shopify_state
    }
    if len(codes) != 1 or not next(iter(codes)):
        raise EnviaAPIError("ENVIA_SHOPIFY_STATE_UNRESOLVED")
    state_code = next(iter(codes))

    result = client.post(
        client.shipping_base, "/locate", {"city": city, "state": state_code, "country": "CO"}
    )
    if (
        not re.fullmatch(r"\d{8}", str(result.get("city") or ""))
        or result.get("state") != state_code
        or _city_key(result.get("name")) != _city_key(city)
    ):
        raise EnviaAPIError("ENVIA_CITY_IDENTITY_UNRESOLVED")
    return {"city": result["city"], "state": state_code}
