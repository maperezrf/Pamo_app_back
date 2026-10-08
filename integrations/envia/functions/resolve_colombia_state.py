import unicodedata

from ..client import EnviaAPIError, EnviaClient

# Alias que no coinciden con el nombre de Envía (`queries.envia.com/state`).
ALIASES = {"BOGOTA D C": "BOGOTA DC", "BOGOTA D.C.": "BOGOTA DC", "BOGOTA, D.C.": "BOGOTA DC", "DISTRITO CAPITAL": "BOGOTA DC"}


def _key(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().upper()
    text = " ".join(text.replace(",", " ").replace(".", " ").split())
    return ALIASES.get(text, text)


def resolve_colombia_state(name_or_code):
    """Código de departamento de Envía (`code_2_digits`, ej. Cundinamarca
    "CN", Magdalena "MA", Bogotá "DC") a partir del nombre que trae un canal
    ("CUNDINAMARCA", "Bogotá, D.C.") o del `provinceCode` de Shopify
    ("CUN", "DC", "ANT"). Lo exige Envía Fulfillment en `state.code`
    (verificado el 2026-10-08). Solo lectura (`/state`, catálogo público).
    """
    target = _key(name_or_code)
    states = EnviaClient().get(EnviaClient().queries_base, "/state", params={"country_code": "CO"}).get("data") or []
    for row in states:
        if not isinstance(row, dict) or row.get("country_code") != "CO":
            continue
        candidates = {_key(row.get("name")), _key(row.get("code_shopify")), _key(row.get("code_3_digits")), _key(row.get("code_2_digits"))}
        if target in candidates:
            return row["code_2_digits"]
    raise EnviaAPIError("ENVIA_STATE_UNRESOLVED")
