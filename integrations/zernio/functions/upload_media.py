from ..client import ZernioClient


class UploadMediaError(ValueError):
    pass


def upload_media(content, filename, mime_type="application/pdf"):
    """Sube un archivo al almacenamiento de Zernio y devuelve su URL
    pública, para usarla en send_whatsapp_template (`document_url`).

    La URL no pide autenticación (nombre aleatorio) y Zernio borra el
    archivo a los 7 días. Tope práctico ~4,5 MB.

    `content`: bytes del archivo.
    """
    if not isinstance(content, bytes) or not content:
        raise UploadMediaError("ZERNIO_MEDIA_CONTENT_REQUIRED")
    response = ZernioClient().upload_file("media/upload-direct", content, filename, mime_type)
    url = response.get("url") if isinstance(response, dict) else None
    if not url:
        raise UploadMediaError("ZERNIO_MEDIA_URL_MISSING")
    return url
