from django.core.management.base import BaseCommand, CommandError

from config.constants import SHOPIFY_SYNC_SCAN_ENABLED
from catalogo.shopify_sync import (
    CONFIRMATION,
    ShopifySyncError,
    execute_shopify_pilot,
    scan_shopify_sync,
    serialize_sync_run,
)


class Command(BaseCommand):
    help = "Detecta cambios locales y, solo con cuatro compuertas, ejecuta un piloto Shopify Beta."

    def add_arguments(self, parser):
        parser.add_argument("--skus", default="")
        parser.add_argument("--limit", type=int, default=10000)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        if not SHOPIFY_SYNC_SCAN_ENABLED and not options["skus"]:
            self.stdout.write("Detección recurrente desactivada; no se leyó ni escribió Shopify. externalWrites=0.")
            return
        skus = [value.strip().upper() for value in options["skus"].split(",") if value.strip()]
        run = scan_shopify_sync(skus=skus, trigger="SCHEDULED_BETA" if not skus else "CLI_PREVIEW", limit=options["limit"])
        if not options["execute"]:
            data = serialize_sync_run(run, item_limit=0)
            self.stdout.write(self.style.SUCCESS(
                f"Vista previa {run.id}: {data['counts']['ready']} listas, {data['counts']['blocked']} bloqueadas; externalWrites=0."
            ))
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"La ejecución requiere --confirm={CONFIRMATION} y una allowlist exacta.")
        try:
            executed = execute_shopify_pilot(run_id=run.id, skus=skus, confirmation=options["confirm"])
        except ShopifySyncError as error:
            raise CommandError(f"{error.code}: {error}") from error
        self.stdout.write(self.style.SUCCESS(
            f"Piloto {executed.id}: {executed.succeeded_count} correctas, {executed.failed_count} fallidas, externalWrites={executed.external_writes}."
        ))
