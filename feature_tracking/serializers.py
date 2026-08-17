from rest_framework import serializers

from .models import GovernancePrototipo


class GovernancePrototipoSerializer(serializers.ModelSerializer):
    """Los campos de merge son de solo lectura acá: solo el webhook de
    GitHub (`webhooks.py` + `functions/procesar_webhook_github_merge.py`)
    puede escribirlos -- una sola fuente de verdad para el estado de merge."""

    class Meta:
        model = GovernancePrototipo
        fields = [
            "id",
            "nombre",
            "descripcion",
            "estado",
            "ambiente",
            "url_github",
            "creado_por",
            "creado_en",
            "actualizado_en",
            "merged_to_desarrollo",
            "merged_to_produccion",
            "url_merge_request",
            "github_event_id",
            "ultima_actualizacion_webhook",
        ]
        read_only_fields = [
            "id",
            "creado_en",
            "actualizado_en",
            "merged_to_desarrollo",
            "merged_to_produccion",
            "url_merge_request",
            "github_event_id",
            "ultima_actualizacion_webhook",
        ]
