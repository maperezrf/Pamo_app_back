import json
import sys
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import IntegrationReadStatus, InventorySourceSnapshot, SiigoProductSnapshot


def amount(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise CommandError("Siigo entregó una cantidad no numérica.") from error


class Command(BaseCommand):
    help = "Enriquece el snapshot Siigo local con una sonda sanitizada de solo lectura."

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede persistir en SQLite local.")
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as error:
            raise CommandError(f"JSON inválido: {error}") from error
        observed_at = parse_datetime((payload.get("source") or {}).get("observed_at") or "") or timezone.now()
        updated = inventory_rows = 0
        for probe in payload.get("probes") or []:
            sku = str(probe.get("sku") or "").strip()
            snapshot = SiigoProductSnapshot.objects.filter(sku=sku).select_related("matched_variant").first()
            if not snapshot:
                continue
            candidates = probe.get("cost_candidates") or []
            snapshot.cost_status = "VERIFIED_FIELD_AVAILABLE" if candidates else "NOT_PROVIDED_BY_PRODUCT_DETAIL"
            snapshot.available_quantity = amount(probe.get("available_quantity"))
            snapshot.warehouses = probe.get("warehouses") or []
            snapshot.observed_at = observed_at
            snapshot.evidence_reference = "Siigo GET /v1/products/{id} read-only sanitized probe"
            snapshot.save(update_fields=["cost_status", "available_quantity", "warehouses", "observed_at", "evidence_reference"])
            updated += 1
            if snapshot.matched_variant_id:
                for warehouse in snapshot.warehouses:
                    quantity = amount(warehouse.get("quantity"))
                    if quantity is None:
                        continue
                    InventorySourceSnapshot.objects.update_or_create(
                        variant=snapshot.matched_variant, source_name="Siigo bodega",
                        warehouse_external_id=str(warehouse.get("id") or ""),
                        defaults={
                            "warehouse_name": warehouse.get("name") or "Bodega Siigo sin nombre",
                            "reported_stock": quantity, "reserved_stock": 0, "safety_stock": 0,
                            "available_to_promise": quantity, "stock_unknown": False,
                            "observed_at": observed_at, "freshness_minutes": 1440,
                            "update_method": InventorySourceSnapshot.UpdateMethod.API,
                            "canonical": False, "evidence_reference": "Siigo product detail warehouses",
                        },
                    )
                    inventory_rows += 1
        summary = payload.get("summary") or {}
        IntegrationReadStatus.objects.update_or_create(
            system="SIIGO", capability="verified_cost",
            defaults={
                "status": "PARTIAL" if summary.get("with_cost_candidate") else "MISSING",
                "message": "La sonda de detalle no expuso costo verificable; precio de venta no fue reinterpretado." if not summary.get("with_cost_candidate") else "La sonda expuso candidatos de costo que requieren validación de contrato.",
                "record_count": int(summary.get("with_cost_candidate") or 0),
                "evidence_reference": "GET /v1/products/{id}, 20 productos sanitizados",
                "observed_at": observed_at, "last_success_at": observed_at, "external_writes": 0,
                "details": {"products_probed": summary.get("products_probed", 0), "warehouse_count": summary.get("warehouse_count", 0)},
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Siigo local: {updated} productos enriquecidos, {inventory_rows} niveles por bodega; externalWrites=0."
        ))
