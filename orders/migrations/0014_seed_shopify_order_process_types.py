from django.db import migrations

PROCESS_TYPES = [
    {
        "code": "orders.process_shopify_order_webhook",
        "name": "Procesar aviso de pedido de Shopify",
        "app_label": "orders",
        # Avisos independientes que llegan casi a la vez: con False se
        # perderían. El bloqueo de fila del guardado evita choques.
        "allow_concurrent": True,
    },
    {
        "code": "orders.backfill_shopify_orders",
        "name": "Cargar pedidos de Shopify desde una fecha",
        "app_label": "orders",
        "allow_concurrent": False,
    },
    {
        "code": "orders.reconcile_shopify_orders",
        "name": "Reconciliar pedidos de Shopify",
        "app_label": "orders",
        "allow_concurrent": False,
    },
]


def seed_process_types(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    for process_type in PROCESS_TYPES:
        ProcessType.objects.get_or_create(
            code=process_type["code"],
            defaults={key: value for key, value in process_type.items() if key != "code"},
        )


def remove_process_types(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    ProcessType.objects.filter(code__in=[process_type["code"] for process_type in PROCESS_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0013_shopify_orders_local_copy"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
