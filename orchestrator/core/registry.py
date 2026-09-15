"""Registro declarativo de tipos de proceso -> callable ejecutable.

Cada app dueña de un proceso (ej. `apps/reports/f11`) se registra a sí misma
llamando a `register_process` desde `apps/orchestrator/registrations.py`
(cargado en `OrchestratorConfig.ready()`), o desde su propio `AppConfig.ready()`.
`apps/orchestrator` no importa lógica de negocio directamente.

Contrato del callable registrado (requerimiento 8 del diseño):

    def mi_proceso(params: dict, progress_callback: Callable, cancellation_token) -> None:
        ...

`progress_callback(percent: int, step: str)` se usa para reportar avance.
"""

_REGISTRY = {}


def register_process(code, callable_fn):
    _REGISTRY[code] = callable_fn


def get_process_callable(code):
    return _REGISTRY.get(code)


def is_registered(code):
    return code in _REGISTRY
