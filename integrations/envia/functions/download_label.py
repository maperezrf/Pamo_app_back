import re
from urllib.parse import urlparse

import requests

from config.constants import ENVIA_ALLOWED_CARRIERS, ENVIA_API_TOKEN

from ..client import EnviaAPIError

MAX_LABEL_BYTES = 12 * 1024 * 1024
SAFE_LABEL_HOST_SUFFIXES = (".envia.com", ".envia.co")
STORAGE_HOST = "s3.us-east-2.amazonaws.com"
STORAGE_PATH = r"/enviapaqueteria/uploads/([A-Za-z0-9_-]+)/[A-Za-z0-9_-]+\.pdf"


def download_label(url):
    """PDF de una guía generada (`create_label` -> `label_url`). Portado de
    `pamo-one-engineering` (`EnviaShippingClient.download_label`).

    Solo baja de dominios de Envía (con el token) o de su almacenamiento en
    S3 (`/enviapaqueteria/uploads/<transportadora>/...pdf`, de una
    transportadora permitida), **sin** el token: la credencial de Envía
    nunca viaja a S3. Sin redirecciones, HTTPS, tamaño máximo y firma PDF.
    Es una lectura: no cobra.
    """
    parsed = urlparse(str(url or ""))
    try:
        valid_port = parsed.port in {None, 443}
    except ValueError:
        valid_port = False
    hostname = parsed.hostname or ""
    trusted_envia = any(hostname == suffix[1:] or hostname.endswith(suffix) for suffix in SAFE_LABEL_HOST_SUFFIXES)
    storage = re.fullmatch(STORAGE_PATH, parsed.path)
    allowed = {carrier.lower() for carrier in ENVIA_ALLOWED_CARRIERS}
    trusted_storage = hostname == STORAGE_HOST and storage is not None and storage.group(1).lower() in allowed
    if (
        parsed.scheme != "https" or not valid_port or parsed.username or parsed.password or parsed.fragment
        or not (trusted_envia or trusted_storage)
    ):
        raise EnviaAPIError("ENVIA_LABEL_URL_INVALID")

    headers = {"Accept": "application/pdf"}
    if trusted_envia:
        headers["Authorization"] = f"Bearer {ENVIA_API_TOKEN}"
    response = None
    try:
        response = requests.get(url, headers=headers, timeout=(5, 30), allow_redirects=False, stream=True)
        if response.is_redirect or response.status_code != 200:
            raise EnviaAPIError("ENVIA_LABEL_DOWNLOAD_FAILED")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if len(content) + len(chunk) > MAX_LABEL_BYTES:
                raise EnviaAPIError("ENVIA_LABEL_PDF_INVALID")
            content.extend(chunk)
        if len(content) < 16 or not content.startswith(b"%PDF"):
            raise EnviaAPIError("ENVIA_LABEL_PDF_INVALID")
        return bytes(content)
    except requests.RequestException as error:
        raise EnviaAPIError("ENVIA_LABEL_DOWNLOAD_FAILED") from error
    finally:
        if response is not None:
            response.close()
