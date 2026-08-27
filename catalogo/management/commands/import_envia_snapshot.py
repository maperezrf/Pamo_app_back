import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import IntegrationReadStatus, LogisticsQuoteSnapshot, ProductVariant


def decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise CommandError("El snapshot Envía contiene un costo no numérico.") from error


class Command(BaseCommand):
    help = "Importa una respuesta Envía ya sanitizada a SQLite. No llama Envía ni crea guías."

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
            if sku and len(matches) != 1:
                raise CommandError(f"El SKU {sku} no tiene coincidencia exacta única; no se importó evidencia ambigua.")
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
        self.stdout.write(self.style.SUCCESS(f"Envía local: {imported} evidencias importadas; externalWrites=0."))
