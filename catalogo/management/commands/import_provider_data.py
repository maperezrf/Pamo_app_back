import csv
import hashlib
import io
import json
import sys
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import (
    InventorySourceSnapshot,
    ProviderConfig,
    ProviderDataImport,
    SkuReconciliation,
    SupplierCatalogItem,
    SupplierItemInventorySnapshot,
)


EXPECTED = [
    "sku", "weight_kg", "length_cm", "width_cm", "height_cm",
    "warehouse_external_id", "warehouse_name", "reported_stock",
    "reserved_stock", "safety_stock", "observed_at", "freshness_minutes",
    "update_method", "evidence_reference",
]


def decimal_value(value, *, positive=False, nonnegative=False):
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ValueError("valor no numérico") from error
    if positive and parsed <= 0:
        raise ValueError("debe ser mayor que cero")
    if nonnegative and parsed < 0:
        raise ValueError("no puede ser negativo")
    return parsed


class Command(BaseCommand):
    help = "Importa peso, dimensiones e inventario de proveedor desde CSV a SQLite local."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)
        parser.add_argument("--source-filename", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede persistir en SQLite local.")
        raw = sys.stdin.read()
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        existing = ProviderDataImport.objects.filter(source_sha256=digest).first()
        if existing:
            self.stdout.write(self.style.SUCCESS(
                f"Archivo ya importado: {existing.imported_rows} filas válidas; externalWrites=0."
            ))
            return
        try:
            provider = ProviderConfig.objects.get(name=options["provider"])
        except ProviderConfig.DoesNotExist as error:
            raise CommandError("Proveedor no encontrado en la base local.") from error
        reader = csv.DictReader(io.StringIO(raw))
        missing_headers = [name for name in EXPECTED if name not in (reader.fieldnames or [])]
        if missing_headers:
            raise CommandError(f"Faltan columnas requeridas: {', '.join(missing_headers)}")

        valid = []
        rejected = []
        for row_number, row in enumerate(reader, start=2):
            sku = str(row.get("sku") or "").strip()
            try:
                if not sku:
                    raise ValueError("SKU vacío")
                items = list(SupplierCatalogItem.objects.filter(provider=provider, supplier_sku=sku)[:2])
                if len(items) != 1:
                    raise ValueError("SKU no tiene coincidencia única en el catálogo del proveedor")
                weight = decimal_value(row.get("weight_kg"), positive=True)
                dims = [decimal_value(row.get(name), positive=True) for name in ("length_cm", "width_cm", "height_cm")]
                if any(value is not None for value in dims) and not all(value is not None for value in dims):
                    raise ValueError("las tres dimensiones deben venir juntas")
                reported = decimal_value(row.get("reported_stock"), nonnegative=True)
                reserved = decimal_value(row.get("reserved_stock"), nonnegative=True) or Decimal("0")
                safety = decimal_value(row.get("safety_stock"), nonnegative=True) or Decimal("0")
                observed_at = parse_datetime(str(row.get("observed_at") or "").strip())
                has_any_fact = weight is not None or all(value is not None for value in dims) or reported is not None
                if not has_any_fact:
                    raise ValueError("fila sin peso, dimensiones ni inventario")
                if observed_at is None:
                    raise ValueError("observed_at debe ser una fecha ISO verificable")
                evidence = str(row.get("evidence_reference") or "").strip()
                if not evidence:
                    raise ValueError("falta evidencia_reference")
                warehouse = str(row.get("warehouse_name") or "").strip()
                if reported is not None and not warehouse:
                    raise ValueError("inventario requiere warehouse_name")
                freshness = int(str(row.get("freshness_minutes") or "1440").strip())
                if freshness <= 0:
                    raise ValueError("freshness_minutes debe ser positivo")
                method = str(row.get("update_method") or "FILE").strip().upper()
                if method not in {"API", "FILE", "MANUAL"}:
                    raise ValueError("update_method debe ser API, FILE o MANUAL")
                valid.append({
                    "row_number": row_number, "row": row, "item": items[0], "sku": sku,
                    "weight": weight, "dims": dims, "reported": reported, "reserved": reserved,
                    "safety": safety, "observed_at": observed_at, "evidence": evidence,
                    "warehouse": warehouse, "warehouse_id": str(row.get("warehouse_external_id") or "").strip(),
                    "freshness": freshness, "method": method,
                })
            except (ValueError, TypeError) as error:
                rejected.append({"row": row_number, "sku": sku, "reason": str(error)})

        batch = ProviderDataImport.objects.create(
            provider=provider, source_filename=options["source_filename"], source_sha256=digest,
            imported_rows=len(valid), rejected_rows=len(rejected),
            weight_rows=sum(entry["weight"] is not None for entry in valid),
            dimension_rows=sum(all(value is not None for value in entry["dims"]) for entry in valid),
            inventory_rows=sum(entry["reported"] is not None for entry in valid),
            audit_payload={"headers": reader.fieldnames, "rejections": rejected[:100], "externalWrites": 0},
            external_writes=0,
        )
        for entry in valid:
            item = entry["item"]
            if entry["weight"] is not None:
                item.weight_kg = entry["weight"]
            if all(value is not None for value in entry["dims"]):
                item.dimensions = dict(zip(("length_cm", "width_cm", "height_cm"), (str(value) for value in entry["dims"])))
            if entry["reported"] is not None:
                item.inventory = entry["reported"]
                item.warehouse = entry["warehouse"]
            item.missing_fields = [name for name, value in (
                ("inventory", item.inventory), ("weight", item.weight_kg), ("dimensions", item.dimensions),
            ) if value is None or value == {}]
            evidence_log = list((item.raw_payload or {}).get("physical_evidence") or [])
            evidence_log.append({
                "source_sha256": digest, "row": entry["row_number"],
                "observed_at": entry["observed_at"].isoformat(), "evidence_reference": entry["evidence"],
            })
            item.raw_payload = {**(item.raw_payload or {}), "physical_evidence": evidence_log[-10:]}
            item.save(update_fields=["weight_kg", "dimensions", "inventory", "warehouse", "missing_fields", "raw_payload"])

            if entry["reported"] is None:
                continue
            available = max(Decimal("0"), entry["reported"] - entry["reserved"] - entry["safety"])
            SupplierItemInventorySnapshot.objects.create(
                item=item, import_batch=batch, warehouse_external_id=entry["warehouse_id"],
                warehouse_name=entry["warehouse"], reported_stock=entry["reported"],
                reserved_stock=entry["reserved"], safety_stock=entry["safety"], available_to_promise=available,
                observed_at=entry["observed_at"], freshness_minutes=entry["freshness"],
                update_method=entry["method"], evidence_reference=entry["evidence"],
            )
            match = SkuReconciliation.objects.filter(supplier_item=item, status="EXACT", variant__isnull=False).select_related("variant").first()
            if match:
                InventorySourceSnapshot.objects.update_or_create(
                    variant=match.variant, source_name=f"Proveedor {provider.name}", warehouse_external_id=entry["warehouse_id"],
                    defaults={
                        "provider": provider, "warehouse_name": entry["warehouse"], "reported_stock": entry["reported"],
                        "reserved_stock": entry["reserved"], "safety_stock": entry["safety"],
                        "available_to_promise": available, "stock_unknown": False,
                        "observed_at": entry["observed_at"], "freshness_minutes": entry["freshness"],
                        "update_method": entry["method"], "canonical": False, "evidence_reference": entry["evidence"],
                    },
                )
        self.stdout.write(self.style.SUCCESS(
            f"Datos de proveedor: {len(valid)} filas importadas, {len(rejected)} rechazadas; externalWrites=0."
        ))
