"""Estado humano y dinámico de los conectores del catálogo.

Este módulo no llama APIs ni escribe en sistemas externos. Resume evidencia
local persistida y deja explícita la diferencia entre conexión, archivo y
planificador.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import (
    IntegrationReadStatus,
    ProviderDataImport,
    SodimacCatalogImportBatch,
    SodimacKitImportBatch,
    SupplierCatalogImport,
)


CONNECTOR_DEFINITIONS = (
    {
        "code": "SHOPIFY",
        "label": "Shopify",
        "purpose": "Catálogo, precios e inventario",
        "mode": "Beta · escrituras apagadas",
        "strategy": "Webhook recomendado + lectura de respaldo",
        "cadence_hours": 6,
        "webhook_state": "NO_INSTALADO_EN_ESTE_MODULO",
    },
    {
        "code": "SIIGO",
        "label": "Siigo",
        "purpose": "Productos, costos e inventario contable",
        "mode": "Solo lectura",
        "strategy": "Lectura API periódica",
        "cadence_hours": 6,
    },
    {
        "code": "MERCADO_LIBRE",
        "label": "Mercado Libre",
        "purpose": "Publicaciones, comisión y envío",
        "mode": "Solo lectura",
        "strategy": "Lectura API periódica",
        "cadence_hours": 6,
    },
    {
        "code": "FALABELLA",
        "label": "Falabella",
        "purpose": "Catálogo del canal",
        "mode": "Solo lectura",
        "strategy": "Lectura API periódica",
        "cadence_hours": 6,
    },
    {
        "code": "SODIMAC",
        "label": "Sodimac / Homecenter",
        "purpose": "Catálogo y transporte",
        "mode": "Fuente local",
        "strategy": "Carga de archivo validado",
        "cadence_hours": None,
    },
    {
        "code": "MADECENTRO",
        "label": "Madecentro",
        "purpose": "Catálogo comercial",
        "mode": "Solo lectura",
        "strategy": "Carga local; sin API verificada",
        "cadence_hours": None,
    },
    {
        "code": "RAPPI",
        "label": "Rappi",
        "purpose": "Catálogo futuro",
        "mode": "No conectado",
        "strategy": "Pendiente de conector",
        "cadence_hours": None,
    },
    {
        "code": "ENVIA",
        "label": "Envía",
        "purpose": "Cotizaciones y costos de guías",
        "mode": "Lectura habilitada",
        "strategy": "Cotización por pedido + caché local",
        "cadence_hours": 6,
    },
    {
        "code": "TAUMM",
        "label": "TAUMM",
        "purpose": "Precio e inventario del proveedor",
        "mode": "Lectura oficial Beta · sin escrituras",
        "strategy": "Lectura cada 4 h; la fuente efectiva es el catálogo nocturno oficial",
        "cadence_hours": 4,
        "remote_worker": True,
    },
    {
        "code": "BARU",
        "label": "Barú",
        "purpose": "Listas, costos y medidas del proveedor",
        "mode": "Carga local",
        "strategy": "Carga de archivo validado",
        "cadence_hours": None,
    },
)

SCHEDULED_CONNECTORS = {
    "SHOPIFY": {"command": "refresh_shopify_snapshot", "timeout_seconds": 900},
    "SIIGO": {"command": "refresh_siigo_snapshot", "timeout_seconds": 900},
    "MERCADO_LIBRE": {"command": "refresh_mercadolibre_snapshot", "timeout_seconds": 1800},
    "FALABELLA": {"command": "refresh_falabella_snapshot", "timeout_seconds": 900},
    "TAUMM": {"command": "refresh_taumm_snapshot", "timeout_seconds": 60},
    "ENVIA": {
        "command": "check_envia_connection",
        "args": ["--execute-read"],
        "timeout_seconds": 120,
    },
}


def _maximum(values):
    valid = [value for value in values if value is not None]
    return max(valid) if valid else None


def _latest_file_upload(code):
    if code == "SODIMAC":
        return _maximum(
            [
                SodimacCatalogImportBatch.objects.values_list("created_at", flat=True).first(),
                SodimacKitImportBatch.objects.values_list("created_at", flat=True).first(),
            ]
        )
    if code == "BARU":
        supplier = SupplierCatalogImport.objects.filter(
            provider__name__iexact="Barú"
        ).values_list("imported_at", flat=True).first()
        facts = ProviderDataImport.objects.filter(
            provider__name__iexact="Barú"
        ).values_list("imported_at", flat=True).first()
        return _maximum([supplier, facts])
    return None


def _connection_status(definition, statuses, now):
    last_attempt = _maximum(row.observed_at for row in statuses)
    last_success = _maximum(row.last_success_at for row in statuses)
    last_file_upload = _latest_file_upload(definition["code"])
    cadence = definition.get("cadence_hours")
    stale = bool(
        cadence
        and last_success
        and now > last_success + timedelta(hours=cadence * 2)
    )
    available = any(row.status == IntegrationReadStatus.Status.AVAILABLE for row in statuses)
    incomplete = any(
        row.status
        in {
            IntegrationReadStatus.Status.PARTIAL,
            IntegrationReadStatus.Status.BLOCKED,
            IntegrationReadStatus.Status.MISSING,
            IntegrationReadStatus.Status.NOT_AUTHORIZED,
        }
        for row in statuses
    )
    if stale:
        state = "STALE"
        state_label = "Desactualizada"
    elif available and incomplete:
        state = "PARTIAL"
        state_label = "Parcial"
    elif available:
        state = "CONNECTED"
        state_label = "Conectada"
    elif last_file_upload:
        state = "FILE_AVAILABLE"
        state_label = "Archivo disponible"
    elif statuses:
        state = "BLOCKED"
        state_label = "Bloqueada"
    else:
        state = "DISCONNECTED"
        state_label = "Desconectada"

    next_scheduled_at = (
        last_success + timedelta(hours=cadence)
        if cadence and last_success
        else None
    )
    return {
        **definition,
        "status": state,
        "status_label": state_label,
        "connected": state in {"CONNECTED", "PARTIAL"},
        "stale": stale,
        "last_attempt_at": last_attempt,
        "last_success_at": last_success,
        "last_file_upload_at": last_file_upload,
        "next_scheduled_at": next_scheduled_at,
        "record_count": max(
            [row.record_count or 0 for row in statuses], default=0
        ),
        "capabilities": len(statuses),
        "external_writes": sum(row.external_writes for row in statuses),
    }


def build_connections_workspace():
    now = timezone.now()
    all_statuses = list(IntegrationReadStatus.objects.all())
    scheduler = next(
        (
            row
            for row in all_statuses
            if row.system == "CATALOG" and row.capability == "connector_scheduler"
        ),
        None,
    )
    connections = []
    for definition in CONNECTOR_DEFINITIONS:
        statuses = [
            row for row in all_statuses if row.system.upper() == definition["code"]
        ]
        connections.append(_connection_status(definition, statuses, now))
    return {
        "observed_at": now,
        "scheduler": {
            "status": scheduler.status if scheduler else "NOT_STARTED",
            "status_label": scheduler.get_status_display() if scheduler else "No iniciado",
            "last_heartbeat_at": scheduler.observed_at if scheduler else None,
            "last_success_at": scheduler.last_success_at if scheduler else None,
            "message": scheduler.message if scheduler else "El planificador local todavía no ha registrado un ciclo.",
            "cadence_label": "Revisión cada 5 min; fuentes elegibles cada 6 h",
            "external_writes": scheduler.external_writes if scheduler else 0,
        },
        "connections": connections,
        "external_writes": sum(item["external_writes"] for item in connections),
    }


def scheduled_connector_due(code, *, now=None):
    definition = next(item for item in CONNECTOR_DEFINITIONS if item["code"] == code)
    cadence = definition.get("cadence_hours")
    if not cadence:
        return False
    now = now or timezone.now()
    last_success = _maximum(
        IntegrationReadStatus.objects.filter(system=code).values_list(
            "last_success_at", flat=True
        )
    )
    return last_success is None or now >= last_success + timedelta(hours=cadence)
