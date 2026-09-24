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

**Todos los procesos en segundo plano se registran en un único archivo:
[`orchestrator/registrations.py`](../../orchestrator/registrations.py).** Las
apps de negocio no se autorregistran en su `AppConfig.ready()`; así el
inventario completo de procesos se lee en un solo lugar.

```python
# orchestrator/registrations.py
from orchestrator.core.registry import register_process
from orders.functions.import_falabella_orders import import_falabella_orders
from orders.functions.process_mercadolibre_notification import process_mercadolibre_notification
from orders.functions.recover_mercadolibre_orders import recover_mercadolibre_orders
from customers.functions.reconcile_from_shopify import reconcile_from_shopify
from customers.functions.process_customer_webhook import process_customer_webhook

register_process("orders.import_falabella", import_falabella_orders)
register_process("orders.process_mercadolibre_notification", process_mercadolibre_notification)  # webhook, allow_concurrent=True
register_process("orders.recover_mercadolibre", recover_mercadolibre_orders)  # programado, allow_concurrent=False
register_process("customers.reconcile_shopify", reconcile_from_shopify)
register_process("customers.process_webhook", process_customer_webhook)
```

`OrchestratorConfig.ready()` (`orchestrator/apps.py`) importa este módulo
antes de recuperar huérfanos y arrancar el scheduler. Ese `ready()` solo
corre con `RUN_MAIN=true` (proceso hijo de `runserver`) o con
`ORCHESTRATOR_FORCE_READY` definido; fuera de esos casos el registro queda
vacío y el runner no encuentra el callable.

Límite que se mantiene: `registrations.py` es el **único** punto donde
`orchestrator` importa código de negocio, y solo importa la función pública
registrada de `<app>/functions/`. El resto de `orchestrator/` (`core/`,
`services.py`, `apis.py`, modelos) no importa apps de negocio, y
`registrations.py` nunca importa modelos ni submódulos internos de otra app.

El callable registrado debe tener la firma `(params, progress_callback=None,
cancellation_token=None)`. Además del registro en memoria, el `ProcessType`
correspondiente debe existir en base de datos (data migration de la app
dueña, `/admin/` o la API) con el mismo `code` exacto.

## Rutas actuales

Montadas bajo `/api/orchestrator/` (`config/urls.py`). Protegidas con
`RoleRequiredMixin` (`accounts.permissions`, ver
[`../patterns/AUTHORIZATION.md`](../patterns/AUTHORIZATION.md)); rol permitido
hoy: `Admin` u `Operaciones`. Detalle completo de endpoints en la guía de uso.

## Reglas de cambio

- Un proceso nuevo se registra agregando su `register_process(...)` en
  `orchestrator/registrations.py`; no se crea `ready()` en la app dueña para
  esto.
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
