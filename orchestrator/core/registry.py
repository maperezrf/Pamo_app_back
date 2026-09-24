"""Registro declarativo de tipos de proceso -> callable ejecutable.

Todos los procesos se registran llamando a `register_process` desde
`orchestrator/registrations.py` (cargado en `OrchestratorConfig.ready()`).
Fuera de ese archivo, `orchestrator` no importa lógica de negocio.

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
