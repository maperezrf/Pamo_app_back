from decimal import Decimal


def calculate_available_to_promise(snapshots):
    """No suma fuentes que puedan representar la misma existencia física.

    Se usa solo la fuente canónica vigente. Si falta o es desconocida, bloquea
    el inventario publicable en vez de convertirlo en cero.
    """
    canonical = [snapshot for snapshot in snapshots if snapshot.canonical]
    if len(canonical) != 1 or canonical[0].stock_unknown or canonical[0].reported_stock is None:
        return {"blocked": True, "quantity": None, "reason": "Inventario canónico desconocido o ambiguo."}
    snapshot = canonical[0]
    quantity = max(
        Decimal("0"),
        snapshot.reported_stock - snapshot.reserved_stock - snapshot.safety_stock,
    )
    return {"blocked": False, "quantity": quantity, "reason": f"Fuente canónica: {snapshot.source_name}."}


def allocate_channels(available, policies):
    """Aplica topes sin multiplicar la existencia compartida entre canales."""
    remaining = available
    result = []
    for policy in sorted(policies, key=lambda item: item["priority"]):
        cap = policy.get("cap")
        quantity = min(remaining, cap) if cap is not None else remaining
        result.append({"channel": policy["channel"], "quantity": quantity})
        remaining -= quantity
    return result
