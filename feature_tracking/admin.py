from django.contrib import admin

from .models import GovernancePrototipo


@admin.register(GovernancePrototipo)
class GovernancePrototipoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'estado', 'ambiente', 'merged_to_desarrollo', 'merged_to_produccion', 'creado_por', 'creado_en')
    search_fields = ('nombre', 'url_github')
    list_filter = ('estado', 'ambiente', 'merged_to_desarrollo', 'merged_to_produccion')
    readonly_fields = (
        'id',
        'creado_en',
        'actualizado_en',
        'merged_to_desarrollo',
        'merged_to_produccion',
        'url_merge_request',
        'github_event_id',
        'ultima_actualizacion_webhook',
    )
