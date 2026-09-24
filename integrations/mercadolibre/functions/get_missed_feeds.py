from config.constants import MERCADOLIBRE_CLIENT_ID

from ..client import MercadoLibreClient


def get_missed_feeds(topic):
    """Notificaciones que Mercado Libre no pudo entregar a la URL de
    notificaciones de la app (`GET /missed_feeds?app_id=...&topic=...`).

    Devuelve una lista de `{"resource", "topic", "user_id",
    "application_id", "attempts"}` tal como las guarda Mercado Libre (la
    misma forma que el webhook). Mercado Libre las retiene por tiempo
    limitado.

    Verificado el 2026-09-24 que el endpoint responde `{"messages": [...]}`
    (vacío entonces: aún no había URL de notificaciones configurada). La
    forma de cada mensaje no se vio todavía contra uno real.
    """
    raw = MercadoLibreClient().get("/missed_feeds", {"app_id": MERCADOLIBRE_CLIENT_ID, "topic": topic})
    return [message for message in raw.get("messages") or [] if isinstance(message, dict)]
