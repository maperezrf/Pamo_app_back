from django.db import migrations

CODE = "orders.import_falabella"


def seed_process_type(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    ProcessType.objects.get_or_create(
        code=CODE,
        defaults={
            "name": "Importar pedidos de Falabella a Shopify",
            "app_label": "orders",
            # Dos corridas a la vez sobre los mismos pedidos pendientes
            # podrían duplicar la creación en Shopify -- ver
            # docs/implementations-plans/marketplace-orders-import.md.
            "allow_concurrent": False,
        },
    )


def remove_process_type(apps, schema_editor):
    ProcessType = apps.get_model("orchestrator", "ProcessType")
    ProcessType.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
        ("orchestrator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_process_type, remove_process_type),
    ]
