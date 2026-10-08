from django.db import models


class SiigoToken(models.Model):
    """Token de acceso de Siigo, cacheado -- estado de conexión, nunca un
    dato de negocio (regla en lineamientos-backend.md §3.2.1). Fila única
    (singleton): siempre se lee/escribe id=1, no hace falta pre-sembrarla
    -- integrations/siigo/client.py la crea sola en el primer uso."""

    # Texto sin límite: el JWT de Siigo pasa de 512 caracteres (con
    # CharField(512) el guardado fallaba y ninguna llamada a Siigo salía;
    # visto en producción el 2026-10-05).
    token = models.TextField()
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
