from django.db import models


class MercadoLibreToken(models.Model):
    """Token OAuth2 de Mercado Libre -- estado de conexión, nunca un dato
    de negocio. Fila única (singleton, id=1), igual que SiigoToken.

    A diferencia de Siigo, no se puede pedir un token nuevo con
    credenciales fijas: la fila nace al conectar la cuenta
    (GET /api/integrations/mercadolibre/connect/ -> callback, ver
    integrations/mercadolibre/apis.py) y de ahí en adelante se renueva
    con `refresh_token`, que es de un solo uso y ROTA en cada renovación
    (ver integrations/mercadolibre/client.py). Perder el refresh token
    nuevo obliga a re-autorizar.

    `user_id` es el seller id de la cuenta autorizada (lo devuelve
    `/oauth/token`); lo necesitan las búsquedas por vendedor y la
    validación de notificaciones.
    """

    access_token = models.CharField(max_length=512)
    refresh_token = models.CharField(max_length=512)
    expires_at = models.DateTimeField()
    user_id = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)
