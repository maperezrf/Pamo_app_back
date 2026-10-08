import re

from ..client import MercadoLibreClient

MAX_LABEL_BYTES = 12 * 1024 * 1024

# Estados en los que Mercado Libre entrega la etiqueta del envío. Descargarla
# en `ready_to_print` la pasa a `printed` (igual que imprimirla en el panel);
# en `printed` / `ready_for_pickup` ya estaba impresa y no cambia nada.
# `ready_for_pickup` no está en la documentación de reimpresión, pero
# devolvió un PDF real (pamo-one-engineering, 2026-09-10).
LABEL_STATUS = "ready_to_ship"
LABEL_SUBSTATUSES = ("ready_to_print", "printed", "ready_for_pickup")


class MercadoLibreLabelError(Exception):
    """Mercado Libre respondió, pero no con un PDF de etiqueta válido."""


def is_label_available(shipment):
    """`shipment` normalizado por `get_shipment`. Full no aplica: esas
    etiquetas las gestiona Mercado Libre."""
    return (
        shipment["status"] == LABEL_STATUS
        and shipment["substatus"] in LABEL_SUBSTATUSES
        and shipment["logistic_type"] != "fulfillment"
    )


def get_shipment_label(shipment_id):
    """PDF de la etiqueta de un envío (`GET /shipment_labels`, un solo id).

    Comprobar antes `is_label_available`: fuera de esos estados Mercado
    Libre responde con error (`MercadoLibreAPIError`). Ojo: en
    `ready_to_print` la descarga marca el envío como impreso.

    Devuelve los bytes del PDF. `MercadoLibreLabelError` si la respuesta no
    es un PDF (nunca se guarda HTML o JSON como guía).
    """
    content, content_type = MercadoLibreClient().get_bytes(
        "/shipment_labels",
        params={"shipment_ids": str(shipment_id), "response_type": "pdf"},
        headers={"Accept": "application/pdf"},
    )
    if content_type.split(";", 1)[0].strip().lower() != "application/pdf":
        raise MercadoLibreLabelError(f"Mercado Libre no devolvió un PDF ({content_type or 'sin content-type'})")
    if not 16 <= len(content) <= MAX_LABEL_BYTES or not re.match(rb"%PDF-\d\.\d", content):
        raise MercadoLibreLabelError("La etiqueta de Mercado Libre no es un PDF válido")
    return content
