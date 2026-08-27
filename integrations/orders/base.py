from dataclasses import dataclass


@dataclass(frozen=True)
class ReadOnlyProviderState:
    provider: str
    enabled: bool
    reason: str
    external_writes: int = 0


class ExternalReadDisabled(RuntimeError):
    pass


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

