import hashlib
import json
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import (
    IntegrationReadStatus,
    LogisticsQuoteSnapshot,
    PhysicalEvidenceCandidate,
    PhysicalEvidenceDecision,
    ProductVariant,
)
from catalogo.physical import upsert_candidate


def decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise CommandError("El snapshot Envía contiene un costo no numérico.") from error


class Command(BaseCommand):
    help = "Importa una respuesta Envía ya sanitizada a SQLite. No llama Envía ni crea guías."

    def add_arguments(self, parser):
        parser.add_argument(
            "--approve-ecommerce-package-sku",
            help="Aprueba localmente un consenso de paquete exacto y unitario para un único SKU.",
        )
        parser.add_argument(
            "--allow-unmatched-costs",
            action="store_true",
            help="Importa costos sanitizados sin SKU local como historial no vinculado.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede persistir en SQLite local.")
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as error:
            raise CommandError(f"JSON inválido: {error}") from error
        records = payload.get("records") or []
        if not isinstance(records, list):
            raise CommandError("Se esperaba records como lista.")
        imported = 0
        for row in records:
            sku = str(row.get("sku") or "").strip()
            matches = list(ProductVariant.objects.filter(sku=sku)[:2]) if sku else []
            if sku and len(matches) != 1 and not options.get("allow_unmatched_costs"):
                raise CommandError(f"El SKU {sku} no tiene coincidencia exacta única; no se importó evidencia ambigua.")
            if len(matches) != 1:
                matches = []
            basis = row.get("basis")
            if basis not in {"CHECKOUT_ESTIMATE", "REALIZED_GUIDE"}:
                raise CommandError("basis debe ser CHECKOUT_ESTIMATE o REALIZED_GUIDE.")
            sanitized = {
                "sku": sku, "basis": basis, "destination": row.get("destination") or {},
                "weight_kg": row.get("weight_kg"), "dimensions": row.get("dimensions") or {},
                "carrier": row.get("carrier") or "", "amount": row.get("amount"),
                "observed_at": row.get("observed_at") or "",
                "external_reference_hash": row.get("external_reference_hash") or "",
                "order_reference_hash": row.get("order_reference_hash") or "",
            }
            digest = hashlib.sha256(json.dumps(sanitized, sort_keys=True).encode()).hexdigest()
            LogisticsQuoteSnapshot.objects.update_or_create(fingerprint=digest, defaults={
                "variant": matches[0] if matches else None, "provider": "ENVIA", "basis": basis,
                "status": IntegrationReadStatus.Status.AVAILABLE if row.get("amount") is not None else IntegrationReadStatus.Status.MISSING,
                "destination": row.get("destination") or {}, "weight_kg": decimal_or_none(row.get("weight_kg")),
                "dimensions": row.get("dimensions") or {}, "carrier": row.get("carrier") or "",
                "amount": decimal_or_none(row.get("amount")), "currency": row.get("currency") or "COP",
                "evidence_reference": row.get("evidence_reference") or "Envía sanitized read-only snapshot",
                "external_reference_hash": row.get("external_reference_hash") or "",
                "order_reference_hash": row.get("order_reference_hash") or "",
                "observed_at": parse_datetime(row.get("observed_at") or "") or timezone.now(), "external_writes": 0,
            })
            imported += 1
        approved = 0
        if options.get("approve_ecommerce_package_sku"):
            approved = self._approve_package_consensus(
                payload.get("package_evidence") or [],
                options["approve_ecommerce_package_sku"],
            )
        self.stdout.write(self.style.SUCCESS(
            f"Envía local: {imported} costos importados y {approved} campos de paquete aprobados; externalWrites=0."
        ))

    def _approve_package_consensus(self, rows, requested_sku):
        sku = str(requested_sku or "").strip()
        matches = list(ProductVariant.objects.filter(sku=sku)[:2])
        if len(matches) != 1:
            raise CommandError(f"El SKU {sku} no tiene coincidencia exacta única.")
        variant = matches[0]
        exact_rows = [
            row for row in rows
            if row.get("exact_single_sku") is True
            and str(row.get("sku") or "").strip() == sku
            and len(row.get("products") or []) == 1
            and Decimal(str((row.get("products") or [{}])[0].get("quantity") or 0)) == Decimal("1")
        ]
        if any(not str(row.get("order_reference_hash") or "").strip() for row in exact_rows):
            raise CommandError(f"{sku}: falta la huella sanitizada de pedido; no se puede deduplicar la evidencia.")
        exact = list({row["order_reference_hash"]: row for row in exact_rows}.values())
        if len(exact) < 2:
            raise CommandError(f"{sku}: se requieren al menos 2 paquetes exactos y unitarios; encontrados {len(exact)}.")

        dimensions = []
        weights = []
        for row in exact:
            dims = row.get("dimensions") or {}
            try:
                dimensions.append(tuple(Decimal(str(dims[field])) for field in ("length_cm", "width_cm", "height_cm")))
                weights.append(Decimal(str(row.get("weight_kg"))))
            except (InvalidOperation, TypeError, KeyError) as error:
                raise CommandError(f"{sku}: paquete incompleto o no numérico en evidencia Envía.") from error
        if any(value <= 0 for values in dimensions for value in values) or any(weight <= 0 for weight in weights):
            raise CommandError(f"{sku}: la evidencia contiene peso o dimensiones no positivas.")
        consensus, count = Counter(dimensions).most_common(1)[0]
        if count != len(dimensions):
            raise CommandError(f"{sku}: las dimensiones de paquetes exactos no tienen consenso total; requiere revisión humana.")

        product_weights = []
        for row in exact:
            raw = (row.get("products") or [{}])[0].get("product_weight_kg")
            if raw not in (None, ""):
                product_weights.append(Decimal(str(raw)))
        conservative_weight = max(weights)
        if product_weights and conservative_weight > max(product_weights) * Decimal("3"):
            raise CommandError(
                f"{sku}: peso de paquete {conservative_weight} kg incompatible con peso de producto {max(product_weights)} kg; no se aprobó."
            )
        if conservative_weight > Decimal("10"):
            raise CommandError(f"{sku}: peso de paquete atípico ({conservative_weight} kg); no se aprobó automáticamente.")

        observed_values = [parse_datetime(row.get("observed_at") or "") for row in exact]
        observed_at = max((value for value in observed_values if value), default=timezone.now())
        expires_at = timezone.now() + timedelta(days=30)
        shipped_count = sum(1 for row in exact if row.get("shipped") is True)
        excerpt = (
            f"{len(exact)} paquetes Envía Ecommerce exactos, SKU único y cantidad 1; "
            f"dimensiones {consensus[0]}x{consensus[1]}x{consensus[2]} cm; "
            f"peso observado {min(weights)}-{max(weights)} kg; {shipped_count} con guía. "
            "Configuración operativa provisional; no equivale a medición física."
        )
        values = {
            PhysicalEvidenceCandidate.Field.WEIGHT: (conservative_weight, "KG"),
            PhysicalEvidenceCandidate.Field.LENGTH: (consensus[0], "CM"),
            PhysicalEvidenceCandidate.Field.WIDTH: (consensus[1], "CM"),
            PhysicalEvidenceCandidate.Field.HEIGHT: (consensus[2], "CM"),
        }
        for field, (value, unit) in values.items():
            candidate = upsert_candidate(
                variant=variant,
                supplier_item=None,
                field=field,
                scope=PhysicalEvidenceCandidate.Scope.PACKAGE,
                classification=PhysicalEvidenceCandidate.Classification.CONFIRMED,
                source_type=PhysicalEvidenceCandidate.SourceType.MANUAL,
                source_reference="Envía Ecommerce v4/orders: consenso exacto de configuración de paquete",
                evidence_excerpt=excerpt,
                original_value=value,
                original_unit=unit,
                confidence=Decimal("0.9000") if shipped_count else Decimal("0.8000"),
                identifier_type="SKU",
                identifier_value=sku,
                selector="package_evidence:exact_single_sku",
                extraction_method="ENVIA_ECOMMERCE_EXACT_SINGLE_SKU_CONSENSUS_V1",
                observed_at=observed_at,
                stale_after=expires_at,
            )
            PhysicalEvidenceDecision.objects.update_or_create(
                candidate=candidate,
                actor_label="user-authorized-envia-recovery",
                defaults={
                    "action": PhysicalEvidenceDecision.Action.APPROVE_LOCAL,
                    "reason": "Uso provisional autorizado para cotización no vinculante; 15 kg rechazado por inconsistencia.",
                    "decision_snapshot": {
                        "sku": sku,
                        "sample_count": len(exact),
                        "shipped_count": shipped_count,
                        "provisional": True,
                        "rejected_weight_kg": "15",
                        "externalWrites": 0,
                    },
                    "expires_at": expires_at,
                    "external_writes": 0,
                },
            )
        return len(values)
