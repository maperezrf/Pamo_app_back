from django.db import models


class SiigoToken(models.Model):
    """Token de acceso de Siigo, cacheado -- estado de conexión, nunca un
    dato de negocio (regla en lineamientos-backend.md §3.2.1). Fila única
    (singleton): siempre se lee/escribe id=1, no hace falta pre-sembrarla
    -- integrations/siigo/client.py la crea sola en el primer uso."""

    token = models.CharField(max_length=512)
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
