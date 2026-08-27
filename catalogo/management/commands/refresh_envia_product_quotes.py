import hashlib
import json
import os
import subprocess
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
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


DEFAULT_PROJECT = "1374343d-5da9-4fa9-9f73-9dfc9f1414e0"
DEFAULT_ENVIRONMENT = "beta"
DEFAULT_SERVICE = "pamo-maestro-api-beta"
REQUIRED_ADDRESS_FIELDS = {"name", "phone", "street", "city", "state", "country", "postalCode"}
PACKAGE_FIELDS = {
    "WEIGHT": "weight", "LENGTH": "length", "WIDTH": "width", "HEIGHT": "height",
}


def parse_address(raw, label):
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise CommandError(f"{label} no es JSON válido.") from error
    missing = sorted(field for field in REQUIRED_ADDRESS_FIELDS if not str(value.get(field) or "").strip())
    if missing:
        raise CommandError(f"{label} requiere: {', '.join(missing)}.")
    if str(value.get("country")).upper() == "CO" and len(str(value.get("city") or "")) != 8:
        raise CommandError(f"{label}.city debe ser el código DANE municipal de 8 dígitos para Colombia.")
    return value


def approved_packages():
    now = timezone.now()
    variants = ProductVariant.objects.prefetch_related("physical_candidates__decisions")
    result = []
    for variant in variants:
        values = {}
        evidence = {}
        for candidate in variant.physical_candidates.all():
            if (
                candidate.scope != PhysicalEvidenceCandidate.Scope.PACKAGE
                or candidate.classification != PhysicalEvidenceCandidate.Classification.CONFIRMED
                or candidate.conflict
                or (candidate.stale_after and candidate.stale_after < now)
            ):
                continue
            decision = candidate.decisions.first()
            if not decision or decision.action != PhysicalEvidenceDecision.Action.APPROVE_LOCAL:
                continue
            if decision.expires_at and decision.expires_at < now:
                continue
            values[candidate.field] = candidate.normalized_value
            evidence[candidate.field] = str(candidate.id)
        if set(PACKAGE_FIELDS) <= set(values):
            result.append((variant, values, evidence))
    return result


class Command(BaseCommand):
    help = "Cotiza productos elegibles en Envía sin crear guía; persiste únicamente el snapshot SQLite local."

    def add_arguments(self, parser):
        parser.add_argument("--origin-json")
        parser.add_argument("--destination-json")
        parser.add_argument("--execute-read", action="store_true")
        parser.add_argument("--cache-hours", type=int, default=6)
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--project", default=DEFAULT_PROJECT)
        parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
        parser.add_argument("--service", default=DEFAULT_SERVICE)
        parser.add_argument("--timeout", type=int, default=420)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La cotización Envía solo puede persistir en SQLite local.")
        packages = approved_packages()
        missing_count = ProductVariant.objects.count() - len(packages)
        if not options["execute_read"]:
            self._status(
                IntegrationReadStatus.Status.BLOCKED,
                "Vista previa: falta --execute-read. No se consultó Envía.",
                len(packages), {"eligible": len(packages), "missing_package": missing_count},
            )
            self.stdout.write(f"Envía vista previa: {len(packages)} elegibles y {missing_count} sin paquete confirmado; externalWrites=0.")
            return
        if not packages:
            self._status(
                IntegrationReadStatus.Status.BLOCKED,
                "No hay variantes con peso y medidas de PAQUETE confirmadas y aprobadas.",
                0, {"eligible": 0, "missing_package": missing_count},
            )
            self.stdout.write("Envía: 0 elegibles; no se hizo ninguna solicitud externa y los costos permanecen vacíos. externalWrites=0.")
            return

        origin = parse_address(options["origin_json"], "origin")
        destination = parse_address(options["destination_json"], "destination")
        cutoff = timezone.now() - timedelta(hours=max(options["cache_hours"], 1))
        requests = []
        cached = 0
        for variant, values, evidence in packages[: max(options["limit"], 1)]:
            dimensions = {key: str(values[field]) for field, key in PACKAGE_FIELDS.items() if field != "WEIGHT"}
            weight = values["WEIGHT"]
            if LogisticsQuoteSnapshot.objects.filter(
                variant=variant, provider="ENVIA", basis=LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
                destination=destination, dimensions=dimensions, weight_kg=weight,
                status=IntegrationReadStatus.Status.AVAILABLE, observed_at__gte=cutoff,
            ).exists():
                cached += 1
                continue
            payload = {
                "origin": origin,
                "destination": destination,
                "packages": [{
                    "content": variant.product.title[:120], "amount": 1, "type": "box",
                    "weight": float(weight), "weightUnit": "KG", "lengthUnit": "CM",
                    "dimensions": {key: float(value) for key, value in dimensions.items()},
                    "declaredValue": float(variant.price or 0), "insurance": 0,
                }],
                "shipment": {"type": 1},
                "settings": {"currency": "COP"},
            }
            requests.append({
                "sku": variant.sku, "variant_id": str(variant.id), "payload": payload,
                "destination_snapshot": destination, "package_snapshot": {
                    "weight_kg": str(weight), "dimensions_cm": dimensions, "evidence": evidence,
                },
            })
        if not requests:
            self._status(IntegrationReadStatus.Status.AVAILABLE, "Todas las cotizaciones elegibles están vigentes en caché local.", cached, {"cached": cached})
            self.stdout.write(f"Envía caché: {cached} cotizaciones vigentes; no se consultó la API. externalWrites=0.")
            return

        script_path = Path(settings.BASE_DIR) / "catalogo" / "scripts" / "export_envia_rates.mjs"
        script = script_path.read_text(encoding="utf-8").replace(
            "__QUOTE_REQUESTS_JSON__", json.dumps({"requests": requests}, ensure_ascii=False), 1,
        )
        command = [
            "railway", "ssh", "--project", options["project"], "--environment", options["environment"],
            "--service", options["service"], "--", "node", "--input-type=module",
        ]
        environment = os.environ.copy()
        environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        environment["RAILWAY_AGENT_SESSION"] = "railway-skill-envia-product-quotes"
        completed = subprocess.run(
            command, input=script, text=True, capture_output=True, check=False,
            timeout=max(options["timeout"], 30), env=environment,
        )
        if completed.returncode != 0:
            raise CommandError("Falló la cotización remota; SQLite no fue modificado. " + " | ".join(completed.stderr.splitlines()[-4:])[:900])
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CommandError("Envía no devolvió un resultado sanitizado válido; SQLite no fue modificado.") from error
        imported = blocked = 0
        quoted_variants = set()
        by_variant = {str(variant.id): variant for variant, _, _ in packages}
        with transaction.atomic():
            for row in payload.get("records") or []:
                variant = by_variant.get(str(row.get("variant_id") or ""))
                options_rows = row.get("options") or []
                if row.get("status") != "AVAILABLE" or not variant or not options_rows:
                    blocked += 1
                    continue
                package = row.get("package") or {}
                for option in options_rows:
                    amount = option.get("amount")
                    if amount is None or Decimal(str(amount)) <= 0:
                        continue
                    fingerprint_payload = {
                        "variant": str(variant.id), "destination": row.get("destination") or {},
                        "package": package, "carrier": option.get("carrier"), "service": option.get("service"),
                        "amount": str(amount), "observed_at": row.get("observed_at"),
                    }
                    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
                    LogisticsQuoteSnapshot.objects.update_or_create(fingerprint=fingerprint, defaults={
                        "variant": variant, "provider": "ENVIA", "basis": LogisticsQuoteSnapshot.Basis.CHECKOUT_ESTIMATE,
                        "status": IntegrationReadStatus.Status.AVAILABLE, "destination": row.get("destination") or {},
                        "weight_kg": Decimal(str(package.get("weight_kg"))), "dimensions": package.get("dimensions_cm") or {},
                        "carrier": " / ".join(filter(None, [option.get("carrier"), option.get("service")])),
                        "amount": Decimal(str(amount)), "currency": option.get("currency") or "COP",
                        "evidence_reference": f"Envía /ship/rate non-binding option; {row.get('option_count') or 1} positive options require policy or human selection",
                        "observed_at": parse_datetime(row.get("observed_at") or "") or timezone.now(), "external_writes": 0,
                    })
                    imported += 1
                    quoted_variants.add(str(variant.id))
        status = IntegrationReadStatus.Status.AVAILABLE if quoted_variants else IntegrationReadStatus.Status.BLOCKED
        self._status(status, "Opciones de tarifa no vinculantes guardadas; ninguna transportadora fue elegida automáticamente." if quoted_variants else "Envía no devolvió opciones positivas.", len(quoted_variants), {"quoted_variants": len(quoted_variants), "rate_options": imported, "blocked": blocked, "cached": cached, "missing_package": missing_count})
        self.stdout.write(f"Envía → SQLite local: {len(quoted_variants)} productos, {imported} opciones, {cached} en caché, {blocked} bloqueadas y {missing_count} sin paquete; externalWrites=0.")

    def _status(self, status, message, count, details):
        now = timezone.now()
        IntegrationReadStatus.objects.update_or_create(
            system="ENVIA", capability="product_rate_quote",
            defaults={
                "status": status, "message": message, "evidence_reference": "Envía POST /ship/rate non-binding",
                "record_count": count, "observed_at": now,
                "last_success_at": now if status == IntegrationReadStatus.Status.AVAILABLE else None,
                "external_writes": 0, "details": details,
            },
        )
