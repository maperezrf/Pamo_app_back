"""Preliquidación controlada para facturas Siigo.

Este módulo separa las lecturas de parametrización de la emisión. Ninguna
función de esta fase hace POST de facturas. El objetivo es obtener evidencia
de cliente, documento, vendedor, forma de pago, productos e impuestos antes de
habilitar una escritura externa.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests


SIIGO_BASE_URL = "https://api.siigo.com"
COP = Decimal("0.01")
SIIGO_INVOICE_PAYLOAD_REVISION = "2"


class SiigoPreflightError(Exception):
    def __init__(self, message, *, code="SIIGO_PREFLIGHT_ERROR", details=None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class SiigoWriteError(Exception):
    def __init__(self, message, *, code="SIIGO_WRITE_ERROR", outcome_unknown=False, details=None):
        self.message = message
        self.code = code
        self.outcome_unknown = outcome_unknown
        self.details = details or {}
        super().__init__(message)


def _text(value):
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in raw if not unicodedata.combining(character)).strip().upper()


def _results(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    return []


def _decimal(value):
    return Decimal(str(value))


def _money(value):
    return _decimal(value).quantize(COP, rounding=ROUND_HALF_UP)


def _only(records, *, missing, ambiguous, predicate=lambda item: True):
    candidates = [item for item in records if predicate(item)]
    if not candidates:
        raise SiigoPreflightError(missing, code="SIIGO_CONFIGURATION_MISSING")
    if len(candidates) != 1:
        raise SiigoPreflightError(
            ambiguous,
            code="SIIGO_CONFIGURATION_AMBIGUOUS",
            details={"candidate_ids": [str(item.get("id") or "") for item in candidates]},
        )
    return candidates[0]


def stable_idempotency_key(remittance):
    """Clave alfanumérica <= 30; estable por remisión y versión comercial."""
    number = re.sub(r"[^A-Za-z0-9]", "", remittance.number or "BORRADOR")[:12]
    digest = hashlib.sha256(
        f"{remittance.id}:{remittance.version}:{number}:{SIIGO_INVOICE_PAYLOAD_REVISION}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"PAMO{number}{digest}"[:30]


def customer_policy(nit):
    """Reglas comerciales aprobadas para clientes especiales.

    ReteICA 11,04 se expresa en Colombia por mil: equivale a 1,104 % para
    buscar el impuesto correspondiente en el catálogo de Siigo.
    """
    normalized = re.sub(r"\D", "", str(nit or ""))
    policies = {
        "830047537": {
            "payment_days": 15,
            "retentions": [
                {
                    "type": "RETEFUENTE",
                    "siigo_percentage": Decimal("2.5"),
                    "calculation_percentage": Decimal("2.5"),
                    "configured_id": "13456",
                    "label": "Retefuente 2,5% Compras",
                    "evidence": "Candidato original; Siigo también contiene una copia llamada Compras 2.",
                },
                {
                    "type": "RETEICA",
                    "siigo_percentage": Decimal("11.04"),
                    "calculation_percentage": Decimal("1.104"),
                    "configured_id": "13457",
                    "label": "ReteICA 11,04‰",
                    "evidence": "ID observado en facturas recientes de LAO KAO.",
                },
            ],
        },
    }
    return policies.get(normalized, {"payment_days": 15, "retentions": []})


@dataclass
class SiigoCredentials:
    username: str
    access_key: str
    partner_id: str

    @property
    def complete(self):
        return all([self.username, self.access_key, self.partner_id])


class SiigoReadClient:
    """Cliente HTTP de solo lectura. No expone secretos ni implementa POST fiscal."""

    def __init__(self, credentials: SiigoCredentials, *, session=None, timeout=45):
        if not credentials.complete:
            raise SiigoPreflightError(
                "Faltan las credenciales de solo lectura de Siigo en el proceso local.",
                code="SIIGO_CREDENTIALS_MISSING",
            )
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = timeout
        self._token = None

    def authenticate(self):
        try:
            response = self.session.post(
                f"{SIIGO_BASE_URL}/auth",
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "Partner-Id": self.credentials.partner_id,
                },
                json={"username": self.credentials.username, "access_key": self.credentials.access_key},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise SiigoPreflightError(
                "No fue posible autenticar la consulta de Siigo.",
                code="SIIGO_AUTH_UNAVAILABLE",
            ) from error
        body = response.json() if response.content else {}
        token = body.get("access_token") if isinstance(body, dict) else None
        if not response.ok or not token:
            raise SiigoPreflightError(
                "Siigo rechazó la autenticación de solo lectura.",
                code="SIIGO_AUTH_REJECTED",
                details={"http_status": response.status_code},
            )
        self._token = token

    def get(self, path, *, params=None):
        if not self._token:
            self.authenticate()
        try:
            response = self.session.get(
                f"{SIIGO_BASE_URL}{path}",
                params=params,
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {self._token}",
                    "Partner-Id": self.credentials.partner_id,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise SiigoPreflightError(
                "Falló una consulta de parametrización en Siigo.",
                code="SIIGO_READ_UNAVAILABLE",
            ) from error
        if not response.ok:
            raise SiigoPreflightError(
                "Siigo rechazó una consulta de parametrización.",
                code="SIIGO_READ_REJECTED",
                details={"path": path, "http_status": response.status_code},
            )
        return response.json() if response.content else {}


class SiigoInvoiceWriteClient(SiigoReadClient):
    """Única frontera de escritura fiscal, invocada solo tras todos los gates."""

    def create_invoice(self, payload, *, idempotency_key):
        if not re.fullmatch(r"[A-Za-z0-9]{1,30}", str(idempotency_key or "")):
            raise SiigoWriteError(
                "La clave de idempotencia no cumple el contrato de Siigo.",
                code="SIIGO_IDEMPOTENCY_INVALID",
            )
        if payload.get("stamp", {}).get("send") is not False or payload.get("mail", {}).get("send") is not False:
            raise SiigoWriteError(
                "La primera prueba solo puede crear un borrador sin DIAN ni correo.",
                code="SIIGO_DRAFT_ONLY",
            )
        if not self._token:
            self.authenticate()
        try:
            response = self.session.post(
                f"{SIIGO_BASE_URL}/v1/invoices",
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "Authorization": f"Bearer {self._token}",
                    "Partner-Id": self.credentials.partner_id,
                    "Idempotency-Key": idempotency_key,
                },
                json=payload,
                timeout=120,
            )
        except requests.RequestException as error:
            raise SiigoWriteError(
                "No se confirmó el resultado en Siigo. No reintentes con otra clave.",
                code="SIIGO_WRITE_RESULT_UNKNOWN",
                outcome_unknown=True,
            ) from error
        body = response.json() if response.content else {}
        if not response.ok:
            errors = body.get("Errors") or body.get("errors") or [] if isinstance(body, dict) else []
            safe_errors = [
                str(item.get("Message") or item.get("message") or "Error de validación Siigo")[:300]
                for item in errors[:5]
                if isinstance(item, dict)
            ]
            raise SiigoWriteError(
                safe_errors[0] if safe_errors else "Siigo rechazó la creación del borrador.",
                code="SIIGO_WRITE_REJECTED",
                details={"http_status": response.status_code, "errors": safe_errors},
            )
        return body


def _select_customer(payload, nit):
    normalized = re.sub(r"\D", "", str(nit or ""))
    return _only(
        _results(payload),
        missing=f"El cliente NIT {normalized} no existe activo en Siigo.",
        ambiguous=f"El NIT {normalized} tiene más de un cliente activo en Siigo; indica la sucursal.",
        predicate=lambda item: (
            item.get("active") is not False
            and re.sub(r"\D", "", str((item.get("identification") or ""))) == normalized
            and int(item.get("branch_office") or 0) == 0
        ),
    )


def _select_configured(records, configured_id, *, label, predicate):
    candidates = [item for item in records if item.get("active") is not False and predicate(item)]
    if configured_id:
        matches = [item for item in candidates if str(item.get("id")) == str(configured_id)]
        return _only(
            matches,
            missing=f"El {label} configurado no está activo o no existe en Siigo.",
            ambiguous=f"El {label} configurado no es único.",
        )
    return _only(
        candidates,
        missing=f"No hay un {label} activo compatible en Siigo.",
        ambiguous=f"Hay varios {label} compatibles; configura el identificador exacto antes de facturar.",
    )


def _select_tax(taxes, *, tax_type, percentage, configured_id=""):
    normalized_type = _text(tax_type).replace(" ", "")
    compatible = [item for item in taxes if (
        item.get("active") is not False
        and _text(item.get("type")).replace(" ", "") == normalized_type
        and item.get("percentage") is not None
        and _decimal(item.get("percentage")) == _decimal(percentage)
    )]
    if configured_id:
        compatible = [item for item in compatible if str(item.get("id")) == str(configured_id)]
    return _only(
        compatible,
        missing=f"No existe en Siigo un impuesto activo {tax_type} de {percentage}%.",
        ambiguous=f"Hay más de un impuesto activo {tax_type} de {percentage}% en Siigo.",
    )


def _seller_id(customer, users, configured_id):
    related = customer.get("related_users") or {}
    preferred = str(related.get("seller_id") or configured_id or "")
    if not preferred:
        raise SiigoPreflightError(
            "El cliente no tiene vendedor asignado y no hay vendedor predeterminado configurado.",
            code="SIIGO_SELLER_MISSING",
        )
    active_ids = {str(item.get("id")) for item in users if item.get("active") is not False}
    if preferred not in active_ids:
        raise SiigoPreflightError(
            "El vendedor asignado al cliente no está activo en Siigo.",
            code="SIIGO_SELLER_INACTIVE",
            details={"seller_id": preferred},
        )
    return preferred


def _product_for_sku(payload, sku):
    normalized = _text(sku)
    return _only(
        _results(payload),
        missing=f"El SKU {sku} no existe activo en Siigo.",
        ambiguous=f"El SKU {sku} no es único en Siigo.",
        predicate=lambda item: item.get("active") is not False and _text(item.get("code")) == normalized,
    )


def _tax_summary(items, retentions):
    gross = Decimal("0.00")
    tax_total = Decimal("0.00")
    taxable_base = Decimal("0.00")
    for item in items:
        rate = _decimal(item["tax_percentage"])
        # Siigo define items.price como precio unitario antes de impuestos.
        line_amount = _money(item["quantity"] * item["price"])
        base = line_amount
        tax = line_amount * (rate / Decimal("100"))
        gross += line_amount + tax
        taxable_base += base
        tax_total += tax
    retained = sum((_money(taxable_base * (_decimal(item["calculation_percentage"]) / Decimal("100"))) for item in retentions), Decimal("0.00"))
    return {
        "gross_total": _money(gross),
        "taxable_base": _money(taxable_base),
        "tax_total": _money(tax_total),
        "retentions_total": _money(retained),
        "payment_total": _money(gross - retained),
    }


def build_live_preflight(
    remittance,
    *,
    client,
    document_id="",
    payment_id="",
    default_seller_id="",
    today=None,
):
    """Consulta Siigo y devuelve el payload exacto, siempre con stamp/mail falsos."""
    today = today or date.today()
    policy = customer_policy(remittance.customer.nit)
    customer_list = client.get(
        "/v1/customers",
        params={
            "identification": remittance.customer.nit,
            "branch_office": 0,
            "active": "true",
            "type": "Customer",
        },
    )
    customer = _select_customer(customer_list, remittance.customer.nit)
    customer = client.get(f"/v1/customers/{customer['id']}")

    document = _select_configured(
        _results(client.get("/v1/document-types", params={"type": "FV"})),
        document_id,
        label="tipo de factura electrónica",
        predicate=lambda item: _text(item.get("electronic_type")) not in {"", "NO ELECTRONICA", "NONE"},
    )
    payment = _select_configured(
        _results(client.get("/v1/payment-types", params={"document_type": "FV"})),
        payment_id,
        label="medio de pago a crédito",
        predicate=lambda item: item.get("due_date") is True or "CRED" in _text(item.get("name")),
    )
    users = _results(client.get("/v1/users", params={"page": 1, "page_size": 100}))
    seller_id = _seller_id(customer, users, default_seller_id)
    taxes = _results(client.get("/v1/taxes"))

    retention_records = []
    item_retention_ids = []
    invoice_retention_payload = []
    for retention in policy["retentions"]:
        match = _select_tax(
            taxes,
            tax_type=retention["type"],
            percentage=retention["siigo_percentage"],
            configured_id=retention["configured_id"],
        )
        normalized_type = _text(retention["type"]).replace(" ", "")
        placement = "ITEM_TAX" if normalized_type == "RETEFUENTE" else "INVOICE_RETENTION"
        retention_records.append({
            "id": str(match["id"]),
            "type": retention["type"],
            "siigo_percentage": retention["siigo_percentage"],
            "calculation_percentage": retention["calculation_percentage"],
            "label": retention["label"],
            "evidence": retention["evidence"],
            "placement": placement,
        })
        if placement == "ITEM_TAX":
            item_retention_ids.append(match["id"])
        else:
            invoice_retention_payload.append({"id": match["id"]})

    items = []
    evidence_items = []
    for line in remittance.lines.all():
        if not line.siigo_sku or line.invoice_unit_price is None:
            raise SiigoPreflightError(
                f"La línea {line.line_number} no tiene SKU y precio aprobados.",
                code="SIIGO_LINE_NOT_READY",
            )
        product = _product_for_sku(
            client.get("/v1/products", params={"code": line.siigo_sku, "active": "true"}),
            line.siigo_sku,
        )
        product_taxes = [item for item in (product.get("taxes") or []) if _text(item.get("type")) == "IVA"]
        product_tax = _only(
            product_taxes,
            missing=f"El producto {line.siigo_sku} no tiene IVA configurado en Siigo.",
            ambiguous=f"El producto {line.siigo_sku} tiene más de un IVA; requiere revisión.",
        )
        if line.quantity.normalize().as_tuple().exponent < -2:
            raise SiigoPreflightError(
                f"La cantidad de la línea {line.line_number} tiene más de 2 decimales y Siigo no la acepta.",
                code="SIIGO_QUANTITY_PRECISION_INVALID",
            )
        items.append({
            "code": line.siigo_sku,
            "description": line.invoice_description or line.original_description,
            "quantity": float(line.quantity),
            "price": float(line.invoice_unit_price),
            "taxes": [
                {"id": product_tax["id"]},
                *[{"id": retention_id} for retention_id in item_retention_ids],
            ],
        })
        evidence_items.append({
            "code": line.siigo_sku,
            "product_id": str(product.get("id") or ""),
            "tax_id": str(product_tax.get("id") or ""),
            "tax_percentage": _decimal(product_tax.get("percentage")),
            "tax_included": product.get("tax_included") is True,
            "quantity": line.quantity,
            "price": line.invoice_unit_price,
        })

    summary = _tax_summary(evidence_items, retention_records)
    due_date = today + timedelta(days=int(policy["payment_days"]))
    payload = {
        "document": {"id": document["id"]},
        "date": today.isoformat(),
        "customer": {"identification": remittance.customer.nit, "branch_office": 0},
        "seller": seller_id,
        "items": items,
        "payments": [{
            "id": payment["id"],
            "value": float(summary["payment_total"]),
            "due_date": due_date.isoformat(),
        }],
        "retentions": invoice_retention_payload,
        "stamp": {"send": False},
        "mail": {"send": False},
        "observations": f"PAMO {remittance.number}",
    }
    return {
        "status": "READY_FOR_CONTROLLED_DRAFT",
        "external_writes": 0,
        "idempotency_key": stable_idempotency_key(remittance),
        "customer": {
            "id": str(customer.get("id") or ""),
            "identification": str(customer.get("identification") or remittance.customer.nit),
            "name": " ".join(customer.get("name") or []) if isinstance(customer.get("name"), list) else (customer.get("name") or remittance.customer.name),
            "vat_responsible": customer.get("vat_responsible"),
            "fiscal_responsibilities": customer.get("fiscal_responsibilities") or [],
            "seller_id": seller_id,
        },
        "document": {
            "id": str(document["id"]),
            "name": document.get("name"),
            "prefix": document.get("prefix"),
            "electronic_type": document.get("electronic_type"),
        },
        "payment": {
            "id": str(payment["id"]),
            "name": payment.get("name"),
            "days": int(policy["payment_days"]),
            "due_date": due_date.isoformat(),
        },
        "retentions": retention_records,
        "summary": summary,
        "payload": payload,
    }
