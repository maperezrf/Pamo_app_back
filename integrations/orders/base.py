from dataclasses import dataclass


@dataclass(frozen=True)
class ReadOnlyProviderState:
    provider: str
    enabled: bool
    reason: str
    external_writes: int = 0


class ExternalReadDisabled(RuntimeError):
    pass


class ExternalReadFailed(RuntimeError):
    """Fallo controlado de una lectura externa sin escrituras remotas."""

    def __init__(self, provider, code, status_code=None):
        self.provider = provider
        self.code = code
        self.status_code = status_code
        super().__init__(f"{provider}: {code}")


class ReadOnlyOrdersProvider:
    provider = "unknown"

    def __init__(self, *, enabled=False):
        self.enabled = enabled

    def status(self):
        return ReadOnlyProviderState(
            provider=self.provider,
            enabled=self.enabled,
            reason="ready_read_only" if self.enabled else "disabled_local",
        )

    def fetch(self, *args, **kwargs):
        if not self.enabled:
            raise ExternalReadDisabled(f"{self.provider} está deshabilitado en local")
        raise NotImplementedError("El contrato real se adapta en la siguiente fase controlada.")
