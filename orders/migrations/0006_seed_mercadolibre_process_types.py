from django.db import migrations

PROCESS_TYPES = [
    {
        "code": "orders.process_mercadolibre_notification",
        "name": "Procesar notificación de pedido de Mercado Libre",
        "app_label": "orders",
        # Pueden llegar varias notificaciones a la vez (regla de
        # docs/patterns/PROVIDER_WEBHOOKS.md); los duplicados del mismo
        # envío los evita el reclamo atómico en process_mercadolibre_order.
        "allow_concurrent": True,
    },
    {
        "code": "orders.recover_mercadolibre",
        "name": "Recuperar pedidos de Mercado Libre no llevados a Shopify",
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
        ("orders", "0005_mercadolibre_fields"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
