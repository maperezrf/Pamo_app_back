# Vocabulario propio de la API de Falabella Seller Center -- no son
# secretos, son valores fijos de esa API. STATUS_MAP replica el mapeo que
# ya corre en producción en un proyecto hermano (pamo-one-engineering);
# confirmar contra tu propia cuenta apenas haya credenciales reales, por
# si tu catálogo de estados difiere.

BASE_URL = "https://sellercenter-api.falabella.com/"

STATUS_MAP = {
    "pending": "pending",
    "ready_to_ship": "ready",
    "shipped": "in_transit",
    "delivered": "delivered",
    "failed": "requires_attention",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "returned": "returned",
}
