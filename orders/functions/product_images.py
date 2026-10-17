import io

from PIL import Image

from integrations.shopify.functions.download_image import download_image
from integrations.shopify.functions.get_variant_images_by_skus import get_variant_images_by_skus


def product_image_urls(skus):
    """URL de la imagen de cada SKU en Shopify (el correo las enlaza). Es un
    adorno: si Shopify falla, no hay imágenes y el aviso sigue. Devuelve
    `{sku: url}`."""
    try:
        return get_variant_images_by_skus(skus)
    except Exception:  # noqa: BLE001 -- sin imágenes, el aviso sigue
        return {}


def product_images(skus):
    """Imagen (bytes) de cada SKU, para el PDF que se manda a la bodega. Lo
    que falle (Shopify, el CDN, una imagen dañada) solo deja ese producto
    sin imagen. Devuelve `{sku: bytes}`."""
    images = {}
    for sku, url in product_image_urls(skus).items():
        try:
            content = download_image(url)
            Image.open(io.BytesIO(content)).verify()  # que el PDF no falle al dibujarla
        except Exception:  # noqa: BLE001 -- esa fila va sin imagen
            continue
        images[sku] = content
    return images
