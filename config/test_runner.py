from unittest.mock import patch

from django.test.runner import DiscoverRunner
from requests.adapters import HTTPAdapter


class RealNetworkCallError(RuntimeError):
    """Una prueba intentó una llamada HTTP real a un proveedor."""


def _block_real_request(adapter, request, *args, **kwargs):
    raise RealNetworkCallError(
        f"Llamada HTTP real bloqueada en pruebas: {request.method} {request.url}. "
        "Mockear la función de integración (ver docs/patterns/EXTERNAL_CLIENTS.md)."
    )


class NoNetworkTestRunner(DiscoverRunner):
    """Runner de `manage.py test` que bloquea toda solicitud HTTP real hecha
    con `requests` (el transporte de todos los clientes de `integrations/`).

    Las pruebas usan las credenciales reales de `.env`: una prueba sin mock
    llegaba a Shopify o a Falabella de verdad, y su fila local desaparecía
    con la base de pruebas (pedidos duplicados en la tienda sin vínculo, ver
    docs/apps/orders.md). Se bloquea en `HTTPAdapter.send`, debajo de los
    mocks habituales (`patch` de la función de integración o de
    `requests.post`), que siguen funcionando."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._network_patch = patch.object(HTTPAdapter, "send", _block_real_request)
        self._network_patch.start()

    def teardown_test_environment(self, **kwargs):
        self._network_patch.stop()
        super().teardown_test_environment(**kwargs)
