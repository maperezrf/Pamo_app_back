import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import IntegrationReadStatus


CAPABILITY = "inventory_price_poller"


def _date(value):
    parsed = parse_datetime(str(value or ""))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _safe_error(error):
    if isinstance(error, HTTPError):
        return f"Pamo Maestro respondió HTTP {error.code}."
    if isinstance(error, URLError):
        return "No fue posible conectar con Pamo Maestro Beta."
    return "La lectura TAUMM no pudo completarse; se conservó la última evidencia válida."


class Command(BaseCommand):
    help = "Lee el estado real TAUMM desde Pamo Maestro Beta; nunca escribe en canales."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=os.getenv("PAMO_MAESTRO_API_BETA_URL", ""))
        parser.add_argument("--token", default=os.getenv("PAMO_MAESTRO_API_TOKEN", ""))
        parser.add_argument("--timeout", type=int, default=30)

    def handle(self, *args, **options):
        base_url = str(options["base_url"] or "").strip()
        token = str(options["token"] or "").strip()
        previous = IntegrationReadStatus.objects.filter(
            system="TAUMM", capability=CAPABILITY
        ).first()
        now = timezone.now()
        if not base_url or not token:
            self._record_failure(
                previous,
                now,
                "Falta configurar la lectura segura de Pamo Maestro Beta.",
                "TAUMM_READER_CONFIGURATION_REQUIRED",
            )
            raise CommandError("Faltan PAMO_MAESTRO_API_BETA_URL o PAMO_MAESTRO_API_TOKEN.")

        endpoint = urljoin(base_url.rstrip("/") + "/", "v1/taumm/inventory-price-poller?limit=8")
        request = Request(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=max(5, options["timeout"])) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            self._record_failure(previous, now, _safe_error(error), "TAUMM_READER_FAILED")
            raise CommandError(_safe_error(error)) from error

        if int(payload.get("externalWrites") or 0) != 0:
            self._record_failure(
                previous,
                now,
                "La fuente TAUMM no confirmó externalWrites=0.",
                "TAUMM_UNSAFE_SOURCE",
            )
            raise CommandError("TAUMM no confirmó una lectura segura.")

        runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
        completed = next(
            (run for run in runs if run.get("state") in {"completed", "completed_with_observations"}),
            None,
        )
        products = payload.get("products") if isinstance(payload.get("products"), dict) else {}
        last_checked = _date(products.get("lastCheckedAt"))
        latest_run = runs[0] if runs else None
        configured = bool(payload.get("enabled"))
        available = bool(completed and last_checked)
        status = (
            IntegrationReadStatus.Status.AVAILABLE
            if available
            else IntegrationReadStatus.Status.PARTIAL
            if configured
            else IntegrationReadStatus.Status.BLOCKED
        )
        message = (
            "TAUMM leído desde el catálogo nocturno oficial; precio e inventario disponibles."
            if available
            else "El lector TAUMM está configurado, pero todavía no existe una corrida válida."
            if configured
            else "El lector TAUMM permanece desactivado en Beta."
        )
        IntegrationReadStatus.objects.update_or_create(
            system="TAUMM",
            capability=CAPABILITY,
            defaults={
                "status": status,
                "message": message,
                "evidence_reference": "Pamo Maestro Beta · GET /v1/taumm/inventory-price-poller",
                "record_count": int(products.get("active") or products.get("total") or 0),
                "observed_at": now,
                "last_success_at": last_checked if available else previous.last_success_at if previous else None,
                "external_writes": 0,
                "details": {
                    "source_mode": payload.get("sourceMode"),
                    "source_label": "Catálogo nocturno oficial TAUMM",
                    "interval_hours": int(payload.get("intervalHours") or 4),
                    "enabled": configured,
                    "products": {
                        "total": int(products.get("total") or 0),
                        "active": int(products.get("active") or 0),
                        "missing": int(products.get("missing") or 0),
                        "pending": int(products.get("pending") or 0),
                        "last_checked_at": products.get("lastCheckedAt"),
                        "last_changed_at": products.get("lastChangedAt"),
                    },
                    "latest_run": {
                        key: latest_run.get(key)
                        for key in (
                            "id", "state", "recordsRead", "recordsValid", "recordsInvalid",
                            "priceChanges", "inventoryChanges", "startedAt", "finishedAt",
                        )
                    } if latest_run else None,
                    "external_writes": 0,
                },
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"TAUMM: {int(products.get('active') or 0)} productos activos; externalWrites=0."
            )
        )

    def _record_failure(self, previous, now, message, code):
        IntegrationReadStatus.objects.update_or_create(
            system="TAUMM",
            capability=CAPABILITY,
            defaults={
                "status": IntegrationReadStatus.Status.BLOCKED,
                "message": message,
                "evidence_reference": "Pamo Maestro Beta · lector TAUMM",
                "record_count": previous.record_count if previous else 0,
                "observed_at": now,
                "last_success_at": previous.last_success_at if previous else None,
                "external_writes": 0,
                "details": {"error_code": code, "external_writes": 0},
            },
        )
