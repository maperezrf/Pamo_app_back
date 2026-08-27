from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from .base import ExternalReadDisabled, ExternalReadFailed, ReadOnlyOrdersProvider


@dataclass(frozen=True)
class CanonicalDocument:
    content: bytes
    mime_type: str
    filename: str


class PamoCanonicalOrdersProvider(ReadOnlyOrdersProvider):
    """Cliente GET-only para el modelo canónico de Pedidos de PAMO Maestro."""

    provider = "pamo_canonical"

    def __init__(
        self,
        *,
        base_url,
        api_token,
        enabled=False,
        request_callable=None,
        timeout=(8, 45),
    ):
        super().__init__(enabled=enabled)
        parsed = urlparse(str(base_url or "").strip())
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("La API canónica debe usar HTTPS o loopback local.")
        if not parsed.netloc:
            raise ValueError("La URL de la API canónica no es válida.")
        self.base_url = str(base_url).rstrip("/") + "/"
        self.api_token = str(api_token or "").strip()
        self.request_callable = request_callable or requests.get
        self.timeout = timeout

    def _request(self, path, *, params=None, binary=False, maximum_bytes=12_000_000):
        if not self.enabled:
            raise ExternalReadDisabled("La lectura canónica está deshabilitada en local")
        if not self.api_token:
            raise ExternalReadFailed(self.provider, "CANONICAL_TOKEN_MISSING")
        response = self.request_callable(
            urljoin(self.base_url, path.lstrip("/")),
            params=params,
            headers={"Authorization": f"Bearer {self.api_token}"},
            timeout=self.timeout,
            allow_redirects=False,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise ExternalReadFailed(
                self.provider,
                f"CANONICAL_HTTP_{response.status_code}",
                response.status_code,
            )
        if binary:
            content = bytes(response.content)
            if len(content) > maximum_bytes:
                raise ExternalReadFailed(self.provider, "CANONICAL_DOCUMENT_TOO_LARGE")
            return response, content
        try:
            payload = response.json()
        except ValueError as error:
            raise ExternalReadFailed(self.provider, "CANONICAL_INVALID_JSON") from error
        if not isinstance(payload, dict):
            raise ExternalReadFailed(self.provider, "CANONICAL_INVALID_PAYLOAD")
        return payload

    def export_orders(self, *, from_date, to_date):
        return self._request(
            "/v1/orders/logistics/export",
            params={"from": from_date, "to": to_date},
        )

    def order_detail(self, canonical_order_id):
        return self._request(f"/v1/orders/{canonical_order_id}/logistics")

    def integration_readiness(self):
        return self._request("/v1/orders/logistics/integrations")

    def shipment_document(self, canonical_shipment_id, *, prefer_manual=False):
        endpoint = "document" if prefer_manual else "label"
        response, content = self._request(
            f"/v1/orders/logistics/shipments/{canonical_shipment_id}/{endpoint}",
            binary=True,
        )
        mime_type = str(response.headers.get("content-type", "")).split(";", 1)[0].lower()
        if mime_type not in {"application/pdf", "image/jpeg", "image/png"}:
            raise ExternalReadFailed(self.provider, "CANONICAL_DOCUMENT_TYPE_REJECTED")
        disposition = str(response.headers.get("content-disposition", ""))
        filename = "guia.pdf" if mime_type == "application/pdf" else "guia.png"
        if "filename=" in disposition:
            candidate = disposition.split("filename=", 1)[1].strip().strip('"')
            candidate = candidate.replace("/", "_").replace("\\", "_")
            if candidate:
                filename = candidate[:200]
        return CanonicalDocument(content=content, mime_type=mime_type, filename=filename)
