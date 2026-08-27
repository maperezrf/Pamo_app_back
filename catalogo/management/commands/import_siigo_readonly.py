from decimal import Decimal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogo.models import IntegrationReadStatus, InventorySourceSnapshot, ProductVariant, SiigoProductSnapshot
from config.constants import SIIGO_ACCESS_KEY, SIIGO_PARTNER_ID, SIIGO_USERNAME


class Command(BaseCommand):
    help = "Lee productos Siigo con credenciales en memoria y persiste un snapshot sanitizado solo en SQLite local."

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador de laboratorio solo puede escribir en SQLite local.")
        if not all([SIIGO_USERNAME, SIIGO_ACCESS_KEY, SIIGO_PARTNER_ID]):
            raise CommandError("Las variables Siigo no están disponibles en memoria.")

        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=4, connect=4, read=4, status=4, backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504], allowed_methods={"GET"},
        )))
        try:
            auth = session.post(
                "https://api.siigo.com/auth",
                headers={"accept": "application/json", "content-type": "application/json", "Partner-Id": SIIGO_PARTNER_ID},
                json={"username": SIIGO_USERNAME, "access_key": SIIGO_ACCESS_KEY},
                timeout=30,
            )
        except requests.RequestException as error:
            raise CommandError(f"No fue posible autenticar la lectura Siigo: {error.__class__.__name__}.") from error
        body = auth.json() if auth.content else {}
        token = body.get("access_token")
        if not auth.ok or not token:
            raise CommandError("Siigo rechazó la autenticación de solo lectura.")

        headers = {"accept": "application/json", "Authorization": f"Bearer {token}", "Partner-Id": SIIGO_PARTNER_ID}
        records = []
        for page in range(1, 401):
            try:
                response = session.get(
                    f"https://api.siigo.com/v1/products?page={page}&page_size=100",
                    headers=headers,
                    timeout=90,
                )
            except requests.RequestException as error:
                raise CommandError(f"La lectura Siigo falló en la página {page}: {error.__class__.__name__}.") from error
            if not response.ok:
                raise CommandError(f"Siigo rechazó la página {page} con HTTP {response.status_code}.")
            payload = response.json()
            batch = payload.get("results") if isinstance(payload.get("results"), list) else []
            records.extend(batch)
            if page % 10 == 0:
                self.stdout.write(f"Siigo read-only: {page} páginas, {len(records)} productos; externalWrites=0.")
            if not batch or not (payload.get("_links", {}).get("next") or payload.get("__links", {}).get("next")):
                break

        observed_at = timezone.now()
        created = updated = exact = missing = ambiguous = 0
        seen_ids = set()
        for record in records:
            sku = str(record.get("code") or "").strip()
            if not sku:
                continue
            matches = list(ProductVariant.objects.filter(sku__iexact=sku).exclude(product__status="STALE_LOCAL_SNAPSHOT")[:3])
            if len(matches) == 1:
                match_status = SiigoProductSnapshot.MatchStatus.EXACT_SHOPIFY
                exact += 1
            elif len(matches) > 1:
                match_status = SiigoProductSnapshot.MatchStatus.AMBIGUOUS_SHOPIFY
                ambiguous += 1
            else:
                match_status = SiigoProductSnapshot.MatchStatus.MISSING_SHOPIFY
                missing += 1

            first_price = None
            for price_group in record.get("prices") or []:
                for price_item in price_group.get("price_list") or []:
                    if price_item.get("value") is not None:
                        first_price = Decimal(str(price_item["value"]))
                        break
                if first_price is not None:
                    break
            tax_rate = next((Decimal(str(item["percentage"])) for item in record.get("taxes") or [] if item.get("percentage") is not None), None)
            source_updated = parse_datetime(str((record.get("metadata") or {}).get("last_updated") or ""))
            siigo_id = str(record.get("id") or sku)
            seen_ids.add(siigo_id)
            snapshot, was_created = SiigoProductSnapshot.objects.update_or_create(
                siigo_id=siigo_id,
                defaults={
                    "sku": sku,
                    "name": str(record.get("name") or sku),
                    "active": record.get("active") is not False,
                    "sale_price": first_price,
                    "tax_rate": tax_rate,
                    "tax_included": record.get("tax_included") if isinstance(record.get("tax_included"), bool) else None,
                    "available_quantity": record.get("available_quantity"),
                    "warehouses": record.get("warehouses") if isinstance(record.get("warehouses"), list) else [],
                    "matched_variant": matches[0] if len(matches) == 1 else None,
                    "match_status": match_status,
                    "cost_status": "NOT_PROVIDED_BY_PRODUCT_LIST",
                    "observed_at": observed_at,
                    "source_updated_at": source_updated,
                },
            )
            created += int(was_created)
            updated += int(not was_created)
            if snapshot.matched_variant:
                for warehouse in snapshot.warehouses:
                    InventorySourceSnapshot.objects.update_or_create(
                        variant=snapshot.matched_variant,
                        source_name="Siigo",
                        warehouse_external_id=str(warehouse.get("id") or warehouse.get("name") or "GENERAL"),
                        defaults={
                            "warehouse_name": str(warehouse.get("name") or "Bodega Siigo"),
                            "reported_stock": warehouse.get("quantity"),
                            "reserved_stock": 0,
                            "safety_stock": 0,
                            "available_to_promise": None,
                            "stock_unknown": warehouse.get("quantity") is None,
                            "observed_at": observed_at,
                            "freshness_minutes": 1440,
                            "update_method": InventorySourceSnapshot.UpdateMethod.API,
                            "canonical": False,
                            "evidence_reference": "Siigo API v1 product list; existencia contable, no disponibilidad dropshipping canónica",
                        },
                    )

        if records:
            SiigoProductSnapshot.objects.exclude(siigo_id__in=seen_ids).update(active=False, matched_variant=None)
        active_rows = SiigoProductSnapshot.objects.filter(active=True)
        IntegrationReadStatus.objects.update_or_create(
            system="SIIGO",
            capability="marketplace_catalog_snapshot",
            defaults={
                "status": IntegrationReadStatus.Status.AVAILABLE,
                "message": "Catálogo Siigo completo persistido en SQLite local.",
                "evidence_reference": "Siigo API v1 product list read-only",
                "record_count": active_rows.count(),
                "observed_at": observed_at,
                "last_success_at": observed_at,
                "external_writes": 0,
                "details": {
                    "total": active_rows.count(), "exact": exact, "missing_shopify": missing,
                    "ambiguous": ambiguous, "created": created, "updated": updated, "externalWrites": 0,
                },
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"Siigo read-only → SQLite: {len(records)} leídos, {created} creados, {updated} actualizados; "
            f"SKU Shopify exactos {exact}, ausentes {missing}, ambiguos {ambiguous}; costos verificados 0; externalWrites=0."
        ))
