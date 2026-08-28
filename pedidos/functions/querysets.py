from pedidos.models import Order


def operational_orders():
    """Oculta fixtures sólo cuando ya existe al menos un pedido real."""

    fixture_ids = Order.objects.filter(
        source_snapshot__has_key="localFixture",
        source_snapshot__localFixture=True,
    ).values("pk")
    real_orders = Order.objects.exclude(pk__in=fixture_ids)
    if real_orders.exists():
        return real_orders
    return Order.objects.all()
