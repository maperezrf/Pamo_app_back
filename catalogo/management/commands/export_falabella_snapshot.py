import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree

import requests
from django.core.management.base import BaseCommand, CommandError


def _text(node, path, default=""):
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else default


def _number(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Lee Falabella Seller Center GetProducts y emite JSON sanitizado. No escribe Falabella ni SQLite."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--max-products", type=int, default=50000)

    def handle(self, *args, **options):
        user_id = str(os.environ.get("FALABELLA_USER_ID") or "").strip()
        api_key = str(os.environ.get("FALABELLA_API_KEY") or "").strip()
        if not user_id or not api_key:
            raise CommandError("Faltan las referencias Falabella requeridas en el proceso.")

        limit = max(1, min(options["limit"], 1000))
        maximum = max(1, min(options["max_products"], 50000))
        offset = 0
        records = []
        session = requests.Session()
        complete = False

        while len(records) < maximum:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
            parameters = {
                "Action": "GetProducts",
                "Filter": "all",
                "Format": "XML",
                "GlobalIdentifier": "0",
                "Limit": str(min(limit, maximum - len(records))),
                "Offset": str(offset),
                "Timestamp": timestamp,
                "UserID": user_id,
                "Version": "1.0",
            }
            canonical = "&".join(
                f"{quote(key, safe='~-._')}={quote(value, safe='~-._')}"
                for key, value in sorted(parameters.items())
            )
            parameters["Signature"] = hmac.new(
                api_key.encode("utf-8"), canonical.encode("ascii"), hashlib.sha256
            ).hexdigest()
            try:
                response = session.get(
                    "https://sellercenter-api.falabella.com/",
                    params=parameters,
                    timeout=120,
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                raise CommandError(f"La lectura Falabella falló: {error.__class__.__name__}.") from error
            if not response.ok:
                raise CommandError(f"Falabella rechazó GetProducts con HTTP {response.status_code}.")
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as error:
                raise CommandError("Falabella no entregó XML válido.") from error
            if root.tag.endswith("ErrorResponse"):
                code = _text(root, ".//ErrorCode", "API_ERROR")
                raise CommandError(f"Falabella rechazó GetProducts: {code}.")

            products = root.findall(".//Products/Product")
            for product in products:
                units = product.findall("./BusinessUnits/BusinessUnit")
                unit = next((item for item in units if _text(item, "OperatorCode").upper() == "FACO"), units[0] if units else None)
                price = _number(_text(unit, "SpecialPrice")) if unit is not None else None
                if price is None and unit is not None:
                    price = _number(_text(unit, "SalePrice"))
                if price is None and unit is not None:
                    price = _number(_text(unit, "Price"))
                seller_sku = _text(product, "SellerSku")
                shop_sku = _text(product, "ShopSku")
                records.append({
                    "external_product_id": shop_sku or seller_sku,
                    "external_variant_id": _text(product, "Variant") or seller_sku,
                    "sku": seller_sku,
                    "barcode": _text(product, "ProductId"),
                    "title": _text(product, "Name"),
                    "brand": _text(product, "Brand"),
                    "category": _text(product, "PrimaryCategory"),
                    "state": _text(unit, "Status", _text(product, "QCStatus", "UNKNOWN")) if unit is not None else _text(product, "QCStatus", "UNKNOWN"),
                    "price": price,
                    "inventory_available": _number(_text(unit, "Stock")) if unit is not None else None,
                    "currency": "COP",
                    "url": _text(product, "Url"),
                    "image_url": _text(product, "MainImage"),
                    "payload": {
                        "qc_status": _text(product, "QCStatus"),
                        "content_score": _number(_text(product, "ContentScore")),
                        "is_published": _text(unit, "IsPublished") if unit is not None else "",
                        "operator_code": _text(unit, "OperatorCode") if unit is not None else "",
                    },
                })
            if len(products) < int(parameters["Limit"]):
                complete = True
                break
            offset += len(products)
            if not products:
                complete = True
                break

        sys.stdout.write(json.dumps({
            "channel": "FALABELLA",
            "records": records,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "complete": complete,
            "source": "Falabella Seller Center GetProducts read-only",
            "externalWrites": 0,
        }, ensure_ascii=False, separators=(",", ":")))
