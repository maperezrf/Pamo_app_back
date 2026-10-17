from ..queries import GET_VARIANT_IMAGES_BY_SKUS
from .get_variants_by_skus import search_variants_by_skus


def get_variant_images_by_skus(skus):
    """URL de la imagen de cada SKU: la de la variante o, si no tiene (lo
    normal en la tienda), la principal de su producto. Devuelve
    `{sku: url}`; un SKU sin coincidencia exacta o sin imagen no aparece.
    Mismos lotes y errores que `get_variants_by_skus`."""
    images = {}
    for sku, node in search_variants_by_skus(skus, GET_VARIANT_IMAGES_BY_SKUS).items():
        url = (node.get("image") or {}).get("url") or ((node.get("product") or {}).get("featuredImage") or {}).get("url")
        if url:
            images[sku] = url
    return images
