import uuid

from django.db import models


class GovernancePrototipo(models.Model):
    """Registro de un prototipo/feature y su ciclo de vida a través de
    ambientes. Los campos de merge (`merged_to_*`, `url_merge_request`,
    `github_event_id`, `ultima_actualizacion_webhook`) son fuente única de
    verdad del webhook de GitHub (ver `webhooks.py`) -- no se escriben desde
    los endpoints de creación/actualización (ver `serializers.py`)."""

    ESTADO_CHOICES = [
        ("activo", "Activo"),
        ("archivado", "Archivado"),
    ]

    AMBIENTE_CHOICES = [
        ("desarrollo", "Desarrollo"),
        ("staging", "Staging"),
        ("produccion", "Producción"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre = models.CharField(max_length=200, unique=True)
    descripcion = models.TextField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="activo")
    ambiente = models.CharField(max_length=20, choices=AMBIENTE_CHOICES)
    url_github = models.URLField(blank=True, default="")
    creado_por = models.CharField(max_length=200)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    merged_to_desarrollo = models.BooleanField(default=False)
    merged_to_produccion = models.BooleanField(default=False)
    url_merge_request = models.URLField(blank=True, default="")
    github_event_id = models.CharField(max_length=200, blank=True, default="")
    ultima_actualizacion_webhook = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creado_en']

    def __str__(self):
        return self.nombre
