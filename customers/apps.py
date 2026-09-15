from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'customers'

    def ready(self):
        # Autorregistro en orchestrator -- ver docs/apps/orchestrator.md.
        # orchestrator nunca importa una app de negocio; cada app se
        # registra sola.
        from orchestrator.core.registry import register_process

        from .functions.process_customer_webhook import process_customer_webhook
        from .functions.reconcile_from_shopify import reconcile_from_shopify

        register_process("customers.reconcile_shopify", reconcile_from_shopify)
        register_process("customers.process_webhook", process_customer_webhook)
