from datetime import timedelta

from django.core import signing

from config.constants import BACKEND_PUBLIC_URL

# La cotización vale 10 días; el link dura más porque el cliente la vuelve a
# abrir para pagar o consultar. Si vence, el vendedor copia otro del listado.
LINK_MAX_AGE = timedelta(days=90)
_SALT = "quotes.pdf-link"


class QuoteLinkInvalid(Exception):
    """Link adulterado o que no es de una cotización."""


class QuoteLinkExpired(Exception):
    pass


def quote_link(draft_order_id):
    """Link público al PDF de una cotización, para el cliente, que no tiene
    sesión. Va firmado con la SECRET_KEY: abre solo esa cotización y vence a
    los `LINK_MAX_AGE`. No lleva huella del contenido: el PDF se arma en
    vivo, así que muestra la cotización como esté en Shopify al abrirlo.
    Vacío si falta `BACKEND_PUBLIC_URL`."""
    if not BACKEND_PUBLIC_URL:
        return ""
    token = signing.dumps({"q": str(draft_order_id)}, salt=_SALT)
    return f"{BACKEND_PUBLIC_URL.rstrip('/')}/api/quotes/pdf/{token}/"


def quote_id_from_link(token):
    """Id de la cotización (draft order, sin `gid://`) de un link de
    `quote_link`."""
    try:
        data = signing.loads(token, salt=_SALT, max_age=LINK_MAX_AGE)
    except signing.SignatureExpired as error:
        raise QuoteLinkExpired from error
    except signing.BadSignature as error:
        raise QuoteLinkInvalid from error
    draft_order_id = str(data.get("q") or "") if isinstance(data, dict) else ""
    if not draft_order_id.isdigit():
        raise QuoteLinkInvalid
    return draft_order_id
