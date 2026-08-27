from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalogo.models import PricingPolicy, ProviderConfig


class Command(BaseCommand):
    help = "Crea una hipótesis comercial local editable para Barú; nunca la activa."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La hipótesis de Fase 3 solo se prepara en SQLite local.")
        provider = ProviderConfig.objects.get(name="Barú")
        policy, _ = PricingPolicy.objects.update_or_create(
            name="Barú · hipótesis editable Fase 3",
            defaults={
                "active": False,
                "approval_status": PricingPolicy.ApprovalStatus.HYPOTHESIS,
                "precedence": PricingPolicy.Precedence.SPECIFIC,
                "priority": 50,
                "channel": "SHOPIFY",
                "provider": provider,
                "target_margin_percent": 30,
                "minimum_margin_percent": 20,
                "channel_commission_percent": 2,
                "channel_fixed_charge": 0,
                "logistics_reserve": 12000,
                "reserve_behavior": PricingPolicy.ReserveBehavior.CAP,
                "max_shipping_subsidy": 12000,
                "rounding_increment": 100,
                "simulation_only": True,
                "explanation": (
                    "Hipótesis no aprobada: 30% objetivo, 20% mínimo, 2% de comisión y "
                    "$12.000 como tope de reserva/subsidio. Todos los valores son editables; "
                    "la reserva no se suma automáticamente al precio."
                ),
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Hipótesis local preparada (id={policy.pk}, active=false, externalWrites=0)."
        ))
