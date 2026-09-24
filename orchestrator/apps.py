import os

from django.apps import AppConfig


class OrchestratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "orchestrator"

    def ready(self):
        # Evita doble ejecución por el autoreloader de Django en desarrollo
        # (runserver lanza un proceso padre + uno hijo con RUN_MAIN=true).
        if os.environ.get("RUN_MAIN") != "true" and not os.environ.get(
            "ORCHESTRATOR_FORCE_READY"
        ):
            return

        # Todos los procesos en segundo plano se registran en
        # `registrations.py`, único módulo de `orchestrator` que importa
        # funciones de negocio (ver `docs/apps/orchestrator.md`).
        from . import registrations
        from .core.recovery import recover_orphan_executions
        from .core.scheduler import start_scheduler

        recover_orphan_executions()
        start_scheduler()
