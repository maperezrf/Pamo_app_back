def select_fulfillment_location(lines, priority_location_id=""):
    """Elige UNA bodega que pueda despachar el envío completo -- una guía
    sale de una sola bodega, así que no se parte entre bodegas. Ver
    docs/implementations-plans/shopify-inventory-by-location.md y, para
    envíos con varias órdenes (packs de Mercado Libre),
    docs/implementations-plans/mercadolibre-orders-import.md (decisión E).

    `lines`: uno por ítem del envío,
        {"sku": str, "quantity": int, "tracked": bool,
         "locations": [{"location_id", "name", "available"}]}
    (la forma de `integrations.shopify.functions.get_variant_inventory_by_sku`
    más la cantidad pedida). Si el mismo SKU llega en varias líneas (p. ej.
    dos órdenes del mismo envío), sus cantidades se suman antes de evaluar.

    Regla:
    1. Una bodega "cubre" el pedido si tiene `available >= quantity` para
       cada ítem. Una bodega ausente en `locations` de un ítem cuenta como 0.
    2. Si la bodega prioritaria cubre el pedido, se elige.
    3. Si no, entre las que lo cubren, la de más unidades disponibles
       sumando los SKUs del pedido; empate -> orden alfabético del nombre.
    4. Si ninguna lo cubre, o algún ítem no controla inventario
       (`tracked: False`), es novedad: bodega vacía y `reason` legible.

    Devuelve {"location_id": str, "name": str, "reason": str}; `reason`
    vacío significa bodega asignada. Función pura, sin I/O.
    """
    lines = _merge_by_sku(lines)
    untracked = [line["sku"] for line in lines if not line["tracked"]]
    if untracked:
        return _novedad(f"No controla inventario en Shopify: {', '.join(untracked)}")

    names = {}
    for line in lines:
        for location in line["locations"]:
            names[location["location_id"]] = location["name"]

    covering = [
        location_id
        for location_id in names
        if all(_available(line, location_id) >= line["quantity"] for line in lines)
    ]
    if not covering:
        return _novedad(f"Ninguna bodega tiene el pedido completo: {_describe(lines)}")

    if priority_location_id in covering:
        chosen = priority_location_id
    else:
        chosen = min(
            covering,
            key=lambda location_id: (-sum(_available(line, location_id) for line in lines), names[location_id]),
        )
    return {"location_id": chosen, "name": names[chosen], "reason": ""}


def _merge_by_sku(lines):
    # Mismo SKU = misma variante de Shopify, así que el stock por bodega es
    # el mismo; solo se acumula la cantidad pedida.
    merged = {}
    for line in lines:
        if line["sku"] in merged:
            merged[line["sku"]]["quantity"] += line["quantity"]
        else:
            merged[line["sku"]] = {**line}
    return list(merged.values())


def _available(line, location_id):
    return next(
        (location["available"] for location in line["locations"] if location["location_id"] == location_id),
        0,
    )


def _describe(lines):
    parts = []
    for line in lines:
        stock = ", ".join(
            f"{location['name']} {location['available']}" for location in line["locations"] if location["available"] > 0
        )
        parts.append(f"{line['sku']} x{line['quantity']} ({stock or 'sin stock'})")
    return "; ".join(parts)


def _novedad(reason):
    return {"location_id": "", "name": "", "reason": reason}
