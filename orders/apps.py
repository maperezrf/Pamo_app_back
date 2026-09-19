from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orders'

    def ready(self):
        # Autorregistro en orchestrator -- ver docs/apps/orchestrator.md.
        from orchestrator.core.registry import register_process

        from .functions.import_falabella_orders import import_falabella_orders

        register_process("orders.import_falabella", import_falabella_orders)
