from datetime import date

from django.core.management.base import BaseCommand, CommandError

from orders.functions.assign_web_dispatch import PAID, assign_web_dispatch
from orders.functions.order_listing_format import day_range
from orders.functions.shopify_order_marketplace import SHOPIFY_CHANNEL
from orders.models import ShopifyOrder


class Command(BaseCommand):
    help = (
        "Asigna bodega a los pedidos web (tienda, Addi, cotizaciones) pagados, sin cancelar ni despachar y sin "
        "despacho, creados desde --since (día de Colombia). Sin --apply solo los lista. Solo lee Shopify "
        "(inventario); no avisa a nadie."
    )

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="Fecha AAAA-MM-DD (inclusive).")
        parser.add_argument("--apply", action="store_true", help="Asignar de verdad (sin esto, solo listar).")

    def handle(self, *args, **options):
        try:
            since = date.fromisoformat(options["since"])
        except ValueError as error:
            raise CommandError("--since debe ser AAAA-MM-DD") from error
        start, _ = day_range(since)
        orders = list(
            ShopifyOrder.objects.filter(
                marketplace=SHOPIFY_CHANNEL,
                financial_status=PAID,
                cancelled_at__isnull=True,
                deleted_at__isnull=True,
                dispatch__isnull=True,
                shopify_created_at__gte=start,
            )
            .exclude(fulfillment_status="FULFILLED")
            .order_by("shopify_created_at")
        )
        self.stdout.write(f"{len(orders)} pedidos web por asignar desde {since}.")
        if not options["apply"]:
            for order in orders:
                self.stdout.write(f"  {order.name} ({order.shopify_created_at:%Y-%m-%d})")
            self.stdout.write("Sin --apply no se asignó nada.")
            return
        assigned = novedad = failed = 0
        for order in orders:
            dispatch = assign_web_dispatch(order.shopify_id)
            if dispatch is None:
                failed += 1
                self.stdout.write(f"  {order.name}: error (ver log); se reintenta en la reconciliación")
            elif dispatch.location:
                assigned += 1
                self.stdout.write(f"  {order.name}: {dispatch.location.name}")
            else:
                novedad += 1
                self.stdout.write(f"  {order.name}: novedad ({dispatch.note})")
        self.stdout.write(self.style.SUCCESS(f"Asignados {assigned}, novedad {novedad}, con error {failed}."))
