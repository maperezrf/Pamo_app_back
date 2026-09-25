from django.db import transaction

from ..models import KitComponent, Product
from .export_kits import KIT_COLUMNS
from .import_equivalences import InvalidColumnsError
from .normalize_cell import normalize_cell

SKU_MAX_LENGTH = Product._meta.get_field("sku").max_length


def import_kits(rows):
    """Carga kits en el formato de `export_kits` (una fila por componente:
    `kit_sku`, `component_sku`, `quantity`).

    Por cada `kit_sku` de la carga, REEMPLAZA completo su conjunto de
    componentes (quitar una fila quita ese componente del kit). Crea los
    `Product` que falten y marca el kit con `is_kit=True`. Los kits que no
    vienen en la carga no se tocan.

    Si cualquier fila de un kit es inválida, ese kit entero no se toca --
    nunca queda a medias -- y se informa cada fila con su error. Todo corre
    en una transacción.

    Devuelve `{"created", "updated", "errors"}` (conteos por kit;
    `errors[].row` es la posición 1-based en `rows`). Lanza
    `InvalidColumnsError` si alguna fila trae una columna desconocida.
    """
    unknown = sorted({column for row in rows for column in row} - set(KIT_COLUMNS))
    if unknown:
        raise InvalidColumnsError(unknown)

    kits = {}  # kit_sku → [(position, component_sku, quantity_raw)], en orden de aparición
    errors = []
    for position, row in enumerate(rows, start=1):
        kit_sku = normalize_cell(row.get("kit_sku"))
        if not kit_sku:
            errors.append({"row": position, "error": "kit_sku vacío."})
            continue
        kits.setdefault(kit_sku, []).append((position, normalize_cell(row.get("component_sku")), row.get("quantity")))

    result = {"created": 0, "updated": 0, "errors": errors}
    with transaction.atomic():
        for kit_sku, items in kits.items():
            components, kit_errors = _validate_kit(kit_sku, items, kits)
            if kit_errors:
                errors.extend(kit_errors)
                continue
            _replace_kit(kit_sku, components, result)

    errors.sort(key=lambda error: error["row"])
    return result


def _validate_kit(kit_sku, items, all_kits):
    """Devuelve `({component_sku: quantity}, errores)`. Con un solo error,
    el kit no se toca: los errores de todas sus filas se informan juntos
    para corregir el Excel de una vez."""
    errors = []
    components = {}

    def fail(position, message):
        errors.append({"row": position, "error": f"Kit '{kit_sku}': {message}"})

    first_position = items[0][0]
    if len(kit_sku) > SKU_MAX_LENGTH:
        fail(first_position, "kit_sku supera el largo máximo.")
    kit = Product.objects.filter(sku=kit_sku).first()
    if kit and kit.used_in_kits.exists():
        fail(first_position, "es componente de otro kit; no hay kits anidados.")

    for position, component_sku, quantity_raw in items:
        quantity = _parse_quantity(quantity_raw)
        if not component_sku:
            fail(position, "component_sku vacío.")
        elif len(component_sku) > SKU_MAX_LENGTH:
            fail(position, f"component_sku '{component_sku}' supera el largo máximo.")
        elif component_sku == kit_sku:
            fail(position, "un kit no puede incluirse a sí mismo.")
        elif component_sku in all_kits or Product.objects.filter(sku=component_sku, is_kit=True).exists():
            fail(position, f"el componente '{component_sku}' es un kit; no hay kits anidados.")
        elif component_sku in components:
            fail(position, f"el componente '{component_sku}' está repetido.")
        elif quantity is None:
            fail(position, f"quantity '{quantity_raw}' debe ser un entero mayor que 0.")
        else:
            components[component_sku] = quantity

    return components, errors


def _parse_quantity(value):
    text = normalize_cell(value)
    if not text.isdigit() or int(text) <= 0:
        return None
    return int(text)


def _replace_kit(kit_sku, components, result):
    kit, created = Product.objects.get_or_create(sku=kit_sku, defaults={"is_kit": True})
    was_kit = kit.is_kit and not created
    if not kit.is_kit:
        kit.is_kit = True
        kit.save(update_fields=["is_kit"])

    current = {item.component.sku: item.quantity for item in kit.components.select_related("component")}
    if current == components:
        return

    kit.components.all().delete()
    component_products = {}
    for component_sku in components:
        component_products[component_sku], _ = Product.objects.get_or_create(sku=component_sku)
    KitComponent.objects.bulk_create([
        KitComponent(kit=kit, component=component_products[component_sku], quantity=quantity)
        for component_sku, quantity in components.items()
    ])
    result["updated" if was_kit and current else "created"] += 1
