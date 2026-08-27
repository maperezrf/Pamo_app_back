import time
from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from config.constants import ORDERS_SYNC_FROM, ORDERS_SYNC_INTERVAL_MINUTES


class Command(BaseCommand):
    help = "Mantiene la copia operativa de Pedidos sincronizada desde la API canónica."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval-minutes", type=int, default=ORDERS_SYNC_INTERVAL_MINUTES)

    def handle(self, *args, **options):
        try:
            floor = date.fromisoformat(ORDERS_SYNC_FROM)
        except ValueError as error:
            raise CommandError("ORDERS_SYNC_FROM debe usar YYYY-MM-DD.") from error
        interval = min(max(int(options["interval_minutes"] or 15), 5), 1440)
        while True:
            today = date.today()
            if floor > today:
                raise CommandError("ORDERS_SYNC_FROM no puede estar en el futuro.")
            try:
                call_command(
                    "import_pamo_orders",
                    from_date=floor.isoformat(),
                    to_date=today.isoformat(),
                    workers=6,
                    label_workers=1,
                    verbosity=1,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sincronización canónica correcta {floor.isoformat()}..{today.isoformat()}; externalWrites=0."
                    )
                )
            except Exception as error:  # el worker conserva la copia anterior y reintenta
                self.stderr.write(
                    f"Sincronización canónica falló ({type(error).__name__}); se conserva el último estado correcto."
                )
            if options["once"]:
                return
            time.sleep(interval * 60)
