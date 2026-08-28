from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from os import environ

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from config.constants import (
    EXTERNAL_WRITES_ENABLED,
    ORDERS_EXTERNAL_READS_ENABLED,
    ORDERS_EXTERNAL_WRITES_ENABLED,
)
from integrations.orders.base import ExternalReadFailed
from integrations.orders.canonical import PamoCanonicalOrdersProvider
from pedidos.functions.canonical_import import apply_canonical_snapshot, apply_integration_readiness
from pedidos.functions.querysets import operational_orders
from pedidos.models import IntegrationStatus, Shipment, ShipmentDocument


class Command(BaseCommand):
    help = "Importa Pedidos desde la API canónica en modo GET-only e idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="from_date", required=True)
        parser.add_argument("--to", dest="to_date", required=True)
        parser.add_argument("--workers", type=int, default=6)
        parser.add_argument("--download-labels", action="store_true")
        parser.add_argument("--labels-only", action="store_true")
        parser.add_argument("--status-only", action="store_true")
        parser.add_argument("--label-workers", type=int, default=3)

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("La importación canónica sólo puede escribir en SQLite local.")
        if not ORDERS_EXTERNAL_READS_ENABLED:
            raise CommandError("ORDERS_EXTERNAL_READS_ENABLED debe estar activo para esta lectura explícita.")
        if EXTERNAL_WRITES_ENABLED or ORDERS_EXTERNAL_WRITES_ENABLED:
            raise CommandError("La importación exige ORDERS_EXTERNAL_WRITES_ENABLED=false.")
        try:
            from_date = date.fromisoformat(options["from_date"])
            to_date = date.fromisoformat(options["to_date"])
        except ValueError as error:
            raise CommandError("Las fechas deben usar YYYY-MM-DD.") from error
        if to_date < from_date or to_date - from_date > timedelta(days=93):
            raise CommandError("El rango debe ser positivo y no superar 93 días.")
        workers = min(max(options["workers"], 1), 10)
        base_url = environ.get("PAMO_CANONICAL_API_BASE_URL", "").strip()
        api_token = environ.get("PAMO_API_TOKEN", "").strip()
        provider = PamoCanonicalOrdersProvider(
            base_url=base_url,
            api_token=api_token,
            enabled=True,
        )

        if options["status_only"]:
            apply_integration_readiness(
                readiness=provider.integration_readiness(),
                from_date=from_date.isoformat(),
                to_date=to_date.isoformat(),
            )
            envia_status, _ = IntegrationStatus.objects.get_or_create(provider="envia")
            total_cached = ShipmentDocument.objects.filter(
                uploaded_by="canonical-read-only-import"
            ).count()
            envia_details = dict(envia_status.details or {})
            envia_details["cachedTotal"] = total_cached
            envia_status.records_observed = total_cached
            envia_status.details = envia_details
            envia_status.last_error_code = (
                "SOME_LABELS_UNAVAILABLE" if envia_details.get("unavailable") else envia_status.last_error_code
            )
            envia_status.save()
            self.stdout.write(self.style.SUCCESS("Estado de integraciones actualizado; externalWrites=0."))
            return

        if options["labels_only"]:
            shipments = list(
                Shipment.objects.filter(
                    order__in=operational_orders(),
                    order__placed_at__date__gte=from_date,
                    order__placed_at__date__lte=to_date,
                )
            )
            label_counts = self._cache_labels(
                provider,
                shipments,
                min(max(options["label_workers"], 1), 5),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Caché local de etiquetas completada: "
                    f"nuevas={label_counts['cached']}, "
                    f"existentes={label_counts['already_cached']}, "
                    f"no_disponibles={label_counts['unavailable']}, "
                    f"codigos={label_counts['unavailable_by_code']}, "
                    "externalWrites=0."
                )
            )
            return

        self.stdout.write("Leyendo exportación canónica sin escrituras externas...")
        export_payload = provider.export_orders(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
        )
        rows = export_payload.get("orders") or []
        if len(rows) > 10_000:
            raise CommandError("La exportación supera el límite local de seguridad (10.000 pedidos).")

        details = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {
                executor.submit(provider.order_detail, row.get("id")): row.get("id")
                for row in rows
                if row.get("id")
            }
            for future in as_completed(pending):
                canonical_id = pending[future]
                try:
                    details[str(canonical_id)] = future.result()
                except Exception as error:
                    raise CommandError(
                        f"La lectura se detuvo antes de escribir: detalle no disponible ({type(error).__name__})."
                    ) from error

        counts, shipments = apply_canonical_snapshot(
            export_payload=export_payload,
            details=details,
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
        )
        apply_integration_readiness(
            readiness=provider.integration_readiness(),
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
        )

        label_counts = {"cached": 0, "already_cached": 0, "unavailable": 0}
        if options["download_labels"]:
            label_counts = self._cache_labels(
                provider,
                shipments,
                min(max(options["label_workers"], 1), 5),
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Importación local completada: "
                f"pedidos={counts.get('orders_total', 0)}, "
                f"despachos={counts.get('shipments_total', 0)}, "
                f"eventos_nuevos={counts.get('events_created', 0)}, "
                f"etiquetas_cache={label_counts['cached']}, "
                f"etiquetas_no_disponibles={label_counts['unavailable']}, "
                f"codigos={label_counts.get('unavailable_by_code', {})}, "
                "externalWrites=0."
            )
        )

    def _cache_labels(self, provider, shipments, workers):
        candidates = []
        already_cached = 0
        for shipment in shipments:
            if not shipment.tracking_number:
                continue
            if ShipmentDocument.objects.filter(shipment=shipment).exists():
                already_cached += 1
                continue
            canonical_id = str(shipment.source_snapshot.get("canonical_shipment_id") or "")
            if canonical_id:
                candidates.append((shipment.id, canonical_id))

        downloaded = []
        unavailable = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {
                executor.submit(provider.shipment_document, canonical_id): (shipment_id, canonical_id)
                for shipment_id, canonical_id in candidates
            }
            for future in as_completed(pending):
                shipment_id, _ = pending[future]
                try:
                    downloaded.append((shipment_id, future.result()))
                except ExternalReadFailed as error:
                    unavailable.append((shipment_id, error.code))
                except Exception:
                    unavailable.append((shipment_id, "CANONICAL_LABEL_READ_FAILED"))

        with transaction.atomic():
            for shipment_id, document in downloaded:
                shipment = Shipment.objects.select_for_update().get(id=shipment_id)
                if ShipmentDocument.objects.filter(shipment=shipment).exists():
                    already_cached += 1
                    continue
                digest = sha256(document.content).hexdigest()
                stored = ShipmentDocument(
                    shipment=shipment,
                    original_name=document.filename,
                    mime_type=document.mime_type,
                    size_bytes=len(document.content),
                    sha256=digest,
                    uploaded_by="canonical-read-only-import",
                )
                stored.file.save(document.filename, ContentFile(document.content), save=True)
            now = timezone.now()
            total_cached = ShipmentDocument.objects.filter(
                uploaded_by="canonical-read-only-import"
            ).count()
            envia_status, _ = IntegrationStatus.objects.get_or_create(provider="envia")
            envia_status.last_attempt_at = now
            envia_status.last_success_at = now
            envia_status.state = "canonical_labels_read_only"
            envia_status.last_error_code = "" if not unavailable else "SOME_LABELS_UNAVAILABLE"
            envia_status.records_observed = total_cached
            envia_status.details = {
                "cached": len(downloaded),
                "cachedTotal": total_cached,
                "alreadyCached": already_cached,
                "unavailable": len(unavailable),
                "unavailableByCode": dict(Counter(code for _, code in unavailable)),
                "externalWrites": 0,
            }
            envia_status.save()
        return {
            "cached": len(downloaded),
            "already_cached": already_cached,
            "unavailable": len(unavailable),
            "unavailable_by_code": dict(Counter(code for _, code in unavailable)),
        }
