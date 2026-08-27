from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver([post_save, post_delete])
def invalidate_catalog_cache(sender, **kwargs):
    """Invalidate cached catalog reads after a local catalog model changes."""
    if sender._meta.app_label == "catalogo" and not kwargs.get("raw", False):
        cache.clear()
