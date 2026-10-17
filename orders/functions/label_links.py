import hashlib
from datetime import timedelta

from django.core import signing
from django.utils import timezone

from config.constants import BACKEND_PUBLIC_URL

from ..models import Dispatch

# Lo que tarda una bodega en despachar, con margen para un puente festivo.
LINK_MAX_AGE = timedelta(days=10)
_SALT = "orders.label-link"


class LabelLinkError(Exception):
    """El link no se puede generar (el texto es para el equipo)."""


class LabelLinkInvalid(Exception):
    """Link adulterado, de otra guía o de un despacho que ya no existe."""


class LabelLinkExpired(Exception):
    pass


def label_link(dispatch):
    """Link público a la guía (PDF) del despacho, para quien no tiene
    sesión (la bodega). Va firmado con la SECRET_KEY: abre solo esa guía,
    vence a los `LINK_MAX_AGE` y deja de servir si la guía cambia (la firma
    lleva la huella del PDF). No queda registro en la base."""
    if not BACKEND_PUBLIC_URL:
        raise LabelLinkError("Falta BACKEND_PUBLIC_URL para armar el link de la guía.")
    if not dispatch.label_pdf:
        raise LabelLinkError(f"El despacho {dispatch.pk} no tiene la guía guardada.")
    token = signing.dumps({"d": dispatch.pk, "h": _fingerprint(dispatch.label_pdf)}, salt=_SALT)
    return f"{BACKEND_PUBLIC_URL.rstrip('/')}/api/orders/labels/{token}/"


def links_expire_on():
    """Fecha (dd/mm/aaaa, Colombia) en que vence un link generado ahora."""
    return (timezone.localtime() + LINK_MAX_AGE).strftime("%d/%m/%Y")


def label_from_link(token):
    """(nombre de archivo, bytes) de la guía de un link de `label_link`."""
    try:
        data = signing.loads(token, salt=_SALT, max_age=LINK_MAX_AGE)
    except signing.SignatureExpired as error:
        raise LabelLinkExpired from error
    except signing.BadSignature as error:
        raise LabelLinkInvalid from error
    dispatch = Dispatch.objects.filter(pk=data.get("d")).select_related("shopify_order").first()
    if dispatch is None or not dispatch.label_pdf or _fingerprint(dispatch.label_pdf) != data.get("h"):
        raise LabelLinkInvalid
    order = str(dispatch.shopify_order.name).lstrip("#")
    return f"guia-{order}-{dispatch.tracking_number or dispatch.pk}.pdf", bytes(dispatch.label_pdf)


def _fingerprint(pdf):
    return hashlib.sha256(bytes(pdf)).hexdigest()[:16]
