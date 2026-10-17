from urllib.parse import urlsplit

import requests

MAX_BYTES = 2 * 1024 * 1024


class DownloadImageError(Exception):
    pass


def download_image(url, *, width=240):
    """Baja una imagen del CDN de Shopify (`cdn.shopify.com`), ya reducida
    a `width` px (el CDN la redimensiona con el parámetro `width`). Solo
    acepta ese host, sin redirecciones ni más de `MAX_BYTES`. Devuelve los
    bytes."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or parts.hostname != "cdn.shopify.com":
        raise DownloadImageError(f"Imagen fuera del CDN de Shopify: {parts.hostname or url!r}.")
    response = requests.get(url, params={"width": width}, timeout=(5, 15), allow_redirects=False, stream=True)
    try:
        if response.status_code != 200:
            raise DownloadImageError(f"HTTP {response.status_code} al bajar la imagen.")
        content = b""
        for chunk in response.iter_content(64 * 1024):
            content += chunk
            if len(content) > MAX_BYTES:
                raise DownloadImageError("Imagen demasiado grande.")
        return content
    finally:
        response.close()
