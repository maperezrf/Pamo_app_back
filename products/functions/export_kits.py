from ..models import KitComponent

KIT_COLUMNS = ["kit_sku", "component_sku", "quantity"]


def export_kits():
    """Kits en formato de filas (una por componente), para que el frontend
    los descargue como Excel: `{"columns": [...], "rows": [...]}`. Es el
    mismo formato que acepta `import_kits`."""
    components = KitComponent.objects.select_related("kit", "component").order_by("kit__sku", "component__sku")
    rows = [
        {"kit_sku": item.kit.sku, "component_sku": item.component.sku, "quantity": item.quantity}
        for item in components
    ]
    return {"columns": KIT_COLUMNS, "rows": rows}
