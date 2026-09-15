from __future__ import annotations

import base64
import json

import requests

from config.constants import OPENAI_API_KEY, REMITTANCE_AI_MODEL
from facturacion.functions.supplier_invoice import (
    SupplierInvoiceError,
    normalize_supplier_invoice_payload,
    parse_supplier_invoice_pdf,
)


SUPPLIER_INVOICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines", "globalDiscountPercent", "globalDiscountValue", "otherCharges", "freightCost", "warnings"],
    "properties": {
        "lines": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sku", "quantity", "description", "unitPrice", "totalPrice", "discountPercent", "discountValue", "confidence", "warning"],
                "properties": {
                    "sku": {"type": ["string", "null"]},
                    "quantity": {"type": "number", "exclusiveMinimum": 0},
                    "description": {"type": "string"},
                    "unitPrice": {"type": ["number", "null"], "minimum": 0},
                    "totalPrice": {"type": ["number", "null"], "minimum": 0},
                    "discountPercent": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                    "discountValue": {"type": ["number", "null"], "minimum": 0},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "warning": {"type": ["string", "null"]},
                },
            },
        },
        "globalDiscountPercent": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "globalDiscountValue": {"type": ["number", "null"], "minimum": 0},
        "otherCharges": {"type": ["number", "null"], "minimum": 0},
        "freightCost": {"type": ["number", "null"], "minimum": 0},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


class OpenAISupplierInvoiceReader:
    endpoint = "https://api.openai.com/v1/responses"

    def read_local(self, invoice):
        """Lee primero PDFs digitales sin consumir IA ni presupuesto diario."""
        if invoice.mime_type == "application/pdf":
            return parse_supplier_invoice_pdf(invoice.body)
        return None

    def read(self, invoice):
        locally_parsed = self.read_local(invoice)
        if locally_parsed:
            return locally_parsed
        if not OPENAI_API_KEY:
            raise SupplierInvoiceError(
                "La lectura automática no está configurada. Puedes continuar en captura manual.",
                status_code=503,
                code="AI_UNAVAILABLE",
            )

        encoded = base64.b64encode(invoice.body).decode("ascii")
        data_url = f"data:{invoice.mime_type};base64,{encoded}"
        prompt = "Extrae los productos de esta factura de proveedor como borrador privado y editable. Los costos son de compra y no son precios de venta."
        content = (
            [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url, "detail": "high"},
            ]
            if invoice.mime_type.startswith("image/")
            else [
                {"type": "input_text", "text": prompt},
                {"type": "input_file", "filename": invoice.original_name, "file_data": data_url},
            ]
        )
        try:
            response = requests.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": REMITTANCE_AI_MODEL,
                    "input": [
                        {
                            "role": "system",
                            "content": "Lee únicamente renglones de productos visibles en una factura de proveedor PDF, imagen o Excel. Extrae SKU, cantidad, descripción EN MAYÚSCULAS, precio unitario, total y descuento por línea. Extrae por separado descuento global, otros cargos y flete. Si falta el precio unitario y existe el total del renglón, devuelve unitPrice null. No confundas IVA, subtotal general, total de factura, NIT, número de factura ni códigos del documento con productos. No inventes valores.",
                        },
                        {"role": "user", "content": content},
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "supplier_invoice_lines",
                            "strict": True,
                            "schema": SUPPLIER_INVOICE_SCHEMA,
                        },
                    },
                },
                timeout=(10, 90),
            )
        except requests.RequestException as error:
            raise SupplierInvoiceError(
                "No fue posible conectar con el lector. Conserva el archivo y prueba de nuevo.",
                status_code=502,
                code="AI_CONNECTION_ERROR",
            ) from error
        if not response.ok:
            raise SupplierInvoiceError(
                "El lector rechazó la factura. Puedes continuar en captura manual.",
                status_code=502,
                code="AI_INTERPRETATION_REJECTED",
            )
        try:
            body = response.json()
            raw = body.get("output_text")
            if not raw:
                raw = next(
                    content_item.get("text")
                    for output_item in body.get("output", [])
                    for content_item in output_item.get("content", [])
                    if content_item.get("type") == "output_text"
                )
            return normalize_supplier_invoice_payload(json.loads(raw))
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SupplierInvoiceError(
                "El lector respondió sin productos utilizables. Puedes corregirlos manualmente.",
                status_code=502,
                code="AI_RESPONSE_INVALID",
            ) from error
