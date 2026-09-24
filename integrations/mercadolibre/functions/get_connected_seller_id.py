from ..models import MercadoLibreToken
from ..client import SINGLETON_ID


def get_connected_seller_id():
    """Seller id de la cuenta de Mercado Libre conectada, o `""` si no hay
    cuenta conectada. Solo lee la BD (sin llamar a Mercado Libre): lo usa
    el webhook para validar que una notificación es de esta cuenta sin
    demorar la respuesta."""
    token = MercadoLibreToken.objects.filter(id=SINGLETON_ID).only("user_id").first()
    return token.user_id if token else ""
