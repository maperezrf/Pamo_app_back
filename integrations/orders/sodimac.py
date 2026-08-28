from .base import ReadOnlyOrdersProvider


class SodimacCanonicalProvider(ReadOnlyOrdersProvider):
    provider = "sodimac"

    def fetch(self, *args, **kwargs):
        if not self.enabled:
            return super().fetch(*args, **kwargs)
        raise NotImplementedError(
            "Sodimac debe leer el modelo canónico existente; no se crea un consumidor paralelo."
        )

