import base64
import binascii
import json

from ..client import FalabellaClient

VERSION = "1.0"
MAX_PDF_BYTES = 5 * 1024 * 1024
DOCUMENT_TYPE = "shippingParcel"


class FalabellaDocumentError(Exception):
    """Falabella respondió, pero sin un PDF de guía válido."""


def get_shipping_document(order_item_ids):
    """PDF de la guía del paquete de esos ítems (`GetDocument`,
    `DocumentType=shippingParcel`). Solo lectura: no cambia el estado del
    pedido.

    `order_item_ids`: los `OrderItemId` del paquete (de `GetOrderItems`).
    La guía existe solo cuando los ítems ya tienen paquete (`PackageId`);
    antes, Falabella responde con error (`FalabellaAPIError`).

    El JSON de Seller Center envuelve el documento en `Documents.Document`
    (respuesta real vista por pamo-one-engineering, 2026-09-10); la forma
    documentada lo trae en `Document`. Se aceptan las dos, nunca ambas.

    Devuelve los bytes del PDF. `FalabellaDocumentError` si no es un PDF.
    """
    ids = [int(item_id) for item_id in order_item_ids]
    if not ids:
        raise ValueError("Se necesita al menos un OrderItemId")
    response = FalabellaClient().request(
        "GetDocument",
        VERSION,
        {"DocumentType": DOCUMENT_TYPE, "OrderItemIds": json.dumps(ids, separators=(",", ":"))},
    )
    body = (response.get("SuccessResponse") or {}).get("Body") or {}
    if "Documents" in body and "Document" in body:
        raise FalabellaDocumentError("Respuesta ambigua: trae Document y Documents")
    document = (body.get("Documents") or {}).get("Document") if "Documents" in body else body.get("Document")
    if not isinstance(document, dict) or document.get("DocumentType") not in (DOCUMENT_TYPE, "parcel"):
        raise FalabellaDocumentError("Falabella no devolvió una guía (shippingParcel)")
    mime = str(document.get("MimeType") or "").split(";", 1)[0].strip().lower()
    if mime != "application/pdf":
        raise FalabellaDocumentError(f"La guía de Falabella no es PDF ({mime or 'sin MimeType'})")
    try:
        content = base64.b64decode(document.get("File") or "", validate=True)
    except (ValueError, binascii.Error) as error:
        raise FalabellaDocumentError("La guía de Falabella no es base64 válido") from error
    if not 16 <= len(content) <= MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise FalabellaDocumentError("La guía de Falabella no es un PDF válido")
    return content
