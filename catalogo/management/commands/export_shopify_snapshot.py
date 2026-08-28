import json
import os
import sys
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Lee todo el catálogo Shopify y emite JSON sanitizado. No escribe Shopify ni la base local."

    def add_arguments(self, parser):
        parser.add_argument("--page-size", type=int, default=100)
        parser.add_argument("--max-variants", type=int, default=10000)
        parser.add_argument("--updated-since", default="")

    def handle(self, *args, **options):
        domain = str(os.environ.get("SHOPIFY_STORE_DOMAIN") or "").strip().lower()
        domain = domain.removeprefix("https://").removeprefix("http://").split("/")[0]
        client_id = str(os.environ.get("SHOPIFY_CLIENT_ID") or "").strip()
        client_secret = str(os.environ.get("SHOPIFY_CLIENT_SECRET") or "").strip()
        api_version = str(os.environ.get("SHOPIFY_API_VERSION") or "2026-07").strip()
        if not all([domain, client_id, client_secret]):
            raise CommandError("Faltan las referencias Shopify requeridas en el proceso.")

        try:
            auth = requests.post(
                f"https://{domain}/admin/oauth/access_token",
                json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                timeout=30,
            )
        except requests.RequestException as error:
            raise CommandError(f"No fue posible autenticar la lectura Shopify: {error.__class__.__name__}.") from error
        auth_body = auth.json() if auth.content else {}
        token = auth_body.get("access_token")
        if not auth.ok or not token:
            raise CommandError(f"Shopify rechazó la autenticación de lectura con HTTP {auth.status_code}.")

        query_path = Path(__file__).resolve().parents[2] / "contracts" / "shopify_catalog_read.graphql"
        query = query_path.read_text(encoding="utf-8")
        endpoint = f"https://{domain}/admin/api/{api_version}/graphql.json"
        session = requests.Session()
        headers = {"accept": "application/json", "content-type": "application/json", "x-shopify-access-token": token}
        after = None
        variants = []
        pages = 0
        maximum = max(1, min(options["max_variants"], 50000))
        page_size = max(1, min(options["page_size"], 100))
        throttle_attempts = 0

        updated_since = str(options.get("updated_since") or "").strip()
        updated_query = f"updated_at:>'{updated_since}'" if updated_since else None
        while len(variants) < maximum:
            response = None
            for attempt in range(5):
                try:
                    response = session.post(
                        endpoint,
                        headers=headers,
                        json={"query": query, "variables": {"first": min(page_size, maximum - len(variants)), "after": after, "updatedQuery": updated_query}},
                        timeout=120,
                    )
                except requests.RequestException as error:
                    if attempt == 4:
                        raise CommandError(f"La lectura Shopify falló: {error.__class__.__name__}.") from error
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 4:
                        raise CommandError(f"Shopify agotó los reintentos con HTTP {response.status_code}.")
                    time.sleep(float(response.headers.get("Retry-After") or 2 ** attempt))
                    continue
                break
            if response is None or not response.ok:
                raise CommandError(f"Shopify rechazó la lectura con HTTP {getattr(response, 'status_code', 'N/D')}.")
            body = response.json()
            if body.get("errors"):
                codes = sorted({str((item.get("extensions") or {}).get("code") or "GRAPHQL_ERROR") for item in body["errors"]})
                if set(codes) == {"THROTTLED"} and throttle_attempts < 8:
                    throttle_attempts += 1
                    wait_seconds = min(2 ** throttle_attempts, 30)
                    self.stderr.write(f"Shopify limitó temporalmente la lectura; reintento en {wait_seconds}s sin perder el cursor.")
                    time.sleep(wait_seconds)
                    continue
                raise CommandError(f"Shopify devolvió errores GraphQL sanitizados: {', '.join(codes)}.")
            throttle_attempts = 0
            connection = ((body.get("data") or {}).get("productVariants") or {})
            batch = connection.get("nodes") if isinstance(connection.get("nodes"), list) else []
            variants.extend(batch)
            pages += 1
            page_info = connection.get("pageInfo") or {}
            after = page_info.get("endCursor") if page_info.get("hasNextPage") else None
            self.stderr.write(f"Shopify read-only: {pages} páginas, {len(variants)} variantes; externalWrites=0.")
            if not after or not batch:
                break

        sys.stdout.write(json.dumps({
            "data": {"productVariants": {"nodes": variants, "pageInfo": {"hasNextPage": bool(after), "endCursor": after}}},
            "pages": pages,
            "complete": not bool(after) and not bool(updated_since),
            "incremental": bool(updated_since),
            "source": "Shopify Admin GraphQL read-only",
            "externalWrites": 0,
        }, ensure_ascii=False, separators=(",", ":")))
