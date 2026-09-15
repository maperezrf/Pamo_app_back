# App `orchestrator`

`orchestrator/` es una app técnica compartida (igual que `integrations/`):
ejecuta y programa procesos de negocio en segundo plano, pero no es dueña de
ningún dato de negocio ni conoce la lógica interna de los procesos que
lanza. Guía operativa completa en
[`../patterns/orchestrator-usage-guide.md`](../patterns/orchestrator-usage-guide.md).

## Capacidades

- `ProcessType`: catálogo declarativo de qué procesos existen (`code`,
  `allow_concurrent`, `max_concurrent_global`, `is_active`).
- `ProcessExecution`: una fila por cada corrida (manual o programada), con
  estado, progreso y error.
- `ProcessScheduleConfig`: programaciones (`ONCE_DATE`, `DAILY`, `WEEKLY`,
  `MONTHLY`, `CRON`) traducidas a triggers de APScheduler
  (`orchestrator/core/scheduler.py`).
- Concurrencia y cola FIFO en memoria (`orchestrator/core/concurrency_manager.py`),
  por proceso Django — no es multi-proceso/multi-nodo: si el despliegue usa
  más de un worker WSGI, cada worker tiene su propio estado.
- Cancelación cooperativa (`orchestrator/core/cancellation.py`): el proceso
  registrado debe consultar `cancellation_token.raise_if_cancelled()` en sus
  propios checkpoints.
- Recuperación de ejecuciones huérfanas al reiniciar
  (`orchestrator/core/recovery.py`): `EJECUTANDO`/`CANCELANDO`/`EN_COLA` pasan
  a `INTERRUMPIDO`.

## Cómo se registra un proceso

`orchestrator` **no importa apps de negocio** — mismo límite que ya aplica a
`integrations` (ver [`../architecture/APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md)).
Cada app dueña de un proceso se autorregistra en su propio
`AppConfig.ready()`:

```python
# orders/apps.py
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "orders"

    def ready(self):
        from orchestrator.core.registry import register_process
        from .functions.import_falabella_order import import_falabella_orders

        register_process("orders.import_falabella", import_falabella_orders)
```

El callable registrado debe tener la firma `(params, progress_callback=None,
cancellation_token=None)`. Además del registro en memoria, el `ProcessType`
correspondiente debe existir en base de datos (vía `/admin/` o la API) con
el mismo `code` exacto.

## Rutas actuales

Montadas bajo `/api/orchestrator/` (`config/urls.py`). Protegidas con
`RoleRequiredMixin` (`accounts.permissions`, ver
[`../patterns/AUTHORIZATION.md`](../patterns/AUTHORIZATION.md)); rol permitido
hoy: `Admin` u `Operaciones`. Detalle completo de endpoints en la guía de uso.

## Reglas de cambio

- No mover checkpoints de cancelación a submódulos internos de lógica de
  negocio: van en la función registrada, después de cada paso costoso.
- No guardar aquí datos de negocio (pedidos, clientes, facturas): solo el
  historial de ejecución del proceso. El resultado de negocio lo persiste la
  app dueña del proceso, en su propio modelo.
- El límite global de concurrencia (`ORCHESTRATOR_SETTINGS["MAX_CONCURRENT_EXECUTIONS"]`
  en `settings.py`, por defecto 3) se puede sobrescribir por proceso con
  `ProcessType.max_concurrent_global`.

## Pruebas

`orchestrator/tests/` prueba concurrencia/cola, cancelación, recuperación de
huérfanos, y camino feliz + rechazo de permisos de los endpoints. Sin red ni
hilos reales (se mockea `ThreadProcessRunner.start`).
