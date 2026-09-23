import os

from django.apps import AppConfig


class OrchestratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "orchestrator"

    def ready(self):
        # Evita doble ejecución por el autoreloader de Django en desarrollo
        # (runserver lanza un proceso padre + uno hijo con RUN_MAIN=true).
        # if os.environ.get("RUN_MAIN") != "true" and not os.environ.get(
        #     "ORCHESTRATOR_FORCE_READY"
        # ):
            # return

        # Los procesos de negocio se registran solos: cada app dueña de un
        # proceso llama a `orchestrator.core.registry.register_process(...)`
        # desde su propio `AppConfig.ready()` (ver `docs/apps/orchestrator.md`).
        # `orchestrator` no importa apps de negocio -- mismo límite que ya
        # aplica a `integrations` (ver `docs/architecture/APP_BOUNDARIES.md`).
        from . import registrations
        from .core.recovery import recover_orphan_executions
        from .core.scheduler import start_scheduler

        recover_orphan_executions()
        start_scheduler()
