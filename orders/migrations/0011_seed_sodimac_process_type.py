from django.db import migrations

PROCESS_TYPES = [
    {
        "code": "orders.sync_sodimac",
        "name": "Sincronizar pedidos de Sodimac con Shopify",
        "app_label": "orders",
        # La cola de Sodimac es destructiva: dos lecturas a la vez se
        # repartirían las OC.
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
        ("orders", "0010_marketplace_status_fields"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
