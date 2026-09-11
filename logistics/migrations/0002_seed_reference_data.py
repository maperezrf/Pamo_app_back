from django.db import migrations


def seed_reference_data(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Party = apps.get_model("logistics", "RemittanceParty")
    Favorite = apps.get_model("logistics", "RemittanceFavorite")
    Warehouse = apps.get_model("logistics", "RemittanceWarehouse")

    for role in ["Admin", "Operaciones", "Logistica", "Facturacion"]:
        Group.objects.get_or_create(name=role)

    Warehouse.objects.get_or_create(
        name="El Nuevo Compa",
        defaults={"is_active": True, "is_default": True, "sort_order": 0},
    )

    parties = [
        ("CUSTOMER", "830047537", "LAO KAO S.A.", 0),
        ("SUPPLIER", "900918689", "GRIFOCOL SOLUTIONS SAS", 0),
        ("SUPPLIER", "900931936", "FERRETERIA TAMAYO S.A.S.", 1),
        ("SUPPLIER", "900942194", "HICASA S.A.S", 2),
        ("SUPPLIER", "830101806", "DMAG GAS LTDA", 3),
    ]
    for party_type, nit, name, sort_order in parties:
        party, _ = Party.objects.get_or_create(
            party_type=party_type, nit=nit,
            defaults={"name": name, "is_validated": False},
        )
        Favorite.objects.get_or_create(
            party=party,
            defaults={"is_active": True, "sort_order": sort_order, "requires_validation": True},
        )


def unseed_reference_data(apps, schema_editor):
    pass  # datos históricos y terceros no se eliminan en una reversión automática


class Migration(migrations.Migration):
    dependencies = [("logistics", "0001_initial")]
    operations = [migrations.RunPython(seed_reference_data, unseed_reference_data)]
