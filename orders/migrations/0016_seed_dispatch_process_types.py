from django.db import migrations

PROCESS_TYPES = [
    {
        "code": "orders.sync_dispatch_locations",
        "name": "Sincronizar bodegas de despacho desde Shopify",
        "app_label": "orders",
        "allow_concurrent": False,
    },
    {
        "code": "orders.dispatch_orders",
        "name": "Despachar pedidos a bodegas",
        "app_label": "orders",
        # Una corrida a la vez: los avisos se reclaman igual, pero dos
        # corridas solo competirían por los mismos pedidos.
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
        ("orders", "0015_dispatch_to_warehouses"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
