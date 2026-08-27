from django.apps import AppConfig


class CatalogoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalogo"
    verbose_name = "Catálogo, costos y precios multicanal"

    def ready(self):
        from . import signals  # noqa: F401
