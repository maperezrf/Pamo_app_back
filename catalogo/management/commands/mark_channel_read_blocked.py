from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogo.models import IntegrationReadStatus


class Command(BaseCommand):
    help = "Registra un bloqueo sanitizado de lectura sin borrar el último snapshot correcto."

    def add_arguments(self, parser):
        parser.add_argument("--system", required=True)
        parser.add_argument("--message", required=True)
        parser.add_argument("--evidence", default="API read-only")

    def handle(self, *args, **options):
        system = options["system"].strip().upper()
        previous = IntegrationReadStatus.objects.filter(system=system, capability="marketplace_catalog_snapshot").first()
        IntegrationReadStatus.objects.update_or_create(
            system=system,
            capability="marketplace_catalog_snapshot",
            defaults={
                "status": IntegrationReadStatus.Status.BLOCKED,
                "message": options["message"][:500],
                "evidence_reference": options["evidence"][:300],
                "record_count": previous.record_count if previous else 0,
                "observed_at": timezone.now(),
                "last_success_at": previous.last_success_at if previous else None,
                "external_writes": 0,
                "details": {"preserved_last_snapshot": bool(previous and previous.record_count), "externalWrites": 0},
            },
        )
        self.stdout.write(self.style.WARNING(f"{system}: lectura bloqueada; último snapshot preservado. externalWrites=0."))
