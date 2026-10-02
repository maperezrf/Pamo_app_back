from django.db import migrations

PROCESS_TYPES = [
    {
        "code": "products.upload_sku_equivalences",
        "name": "Cargar equivalencias de SKU validadas en Shopify",
        "app_label": "products",
        # Una segunda carga no falla con 409: queda en cola. Ojo:
        # `max_concurrent_global` se compara con TODAS las ejecuciones
        # activas del orquestador, así que la carga espera si corre cualquier
        # otro proceso (ver docs/implementations-plans/sku-equivalence-upload.md).
        "allow_concurrent": True,
        "max_concurrent_global": 1,
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
        ("products", "0002_sku_upload"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_types, remove_process_types),
    ]
