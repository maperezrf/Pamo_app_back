from django.db import migrations


PROCESS_TYPES = [
    {
        "code": "customers.reconcile_shopify",
        "name": "Reconciliar clientes de Shopify",
        "app_label": "customers",
        "allow_concurrent": False,
    },
    {
        "code": "customers.process_webhook",
        "name": "Procesar webhook de cliente de Shopify",
        "app_label": "customers",
        # Pueden llegar varios webhooks casi al mismo tiempo -- ver
        # docs/patterns/PROVIDER_WEBHOOKS.md. Con allow_concurrent=False se
        # perderían webhooks (409 silencioso).
        "allow_concurrent": True,
    },
]


def seed_process_types(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    for entry in PROCESS_TYPES:
        defaults = {key: value for key, value in entry.items() if key != "code"}
        ProcessType.objects.get_or_create(code=entry["code"], defaults=defaults)


def remove_process_types(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    ProcessType.objects.filter(code__in=[entry["code"] for entry in PROCESS_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0001_initial"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
