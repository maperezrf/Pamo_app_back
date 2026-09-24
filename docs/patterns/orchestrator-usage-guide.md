Guía de uso — app `orchestrator`

Guía operativa completa: cómo registrar un proceso, cómo lanzarlo desde un
endpoint, cómo programarlo por calendario, y documentación de todos los
endpoints expuestos. Para el expediente de la app ver
docs/apps/orchestrator.md.

Índice

Conceptos clave
Cómo registrar un proceso nuevo
Cómo lanzar un proceso desde un endpoint (ejecución manual)
Cómo registrar una ejecución programada (scheduler)
Documentación de endpoints
Estados de una ejecución
Errores comunes y troubleshooting


1. Conceptos clave



Concepto
Qué es
Dónde vive




ProcessType
Catálogo declarativo: qué procesos existen
Tabla en BD (orchestrator/models.py)


Callable registrado
La función Python real que hace el trabajo
En memoria, vía orchestrator/core/registry.py


ProcessExecution
Una fila por cada corrida (manual o programada)
Tabla en BD


ProcessScheduleConfig
Una programación (cuándo disparar un ProcessType)
Tabla en BD


concurrency_manager
Cola FIFO + límite de hilos simultáneos, en memoria
orchestrator/core/concurrency_manager.py


CancellationToken
Objeto que el proceso consulta para saber si lo cancelaron
orchestrator/core/cancellation.py


progress_callback
Función que el proceso llama para avisar % y paso actual
Pasada por orchestrator/core/runner.py



Regla de oro: un proceso existe para el orquestador solo si tiene dos partes, y ambas deben usar el mismo code:

Un registro en la tabla ProcessType (code="orders.import_falabella").
Un register_process("orders.import_falabella", mi_funcion) en orchestrator/registrations.py (único archivo donde se registran todos los procesos en segundo plano).


2. Cómo registrar un proceso nuevo
Paso 1 — Escribe (o adapta) la función de negocio con el contrato exacto
def mi_proceso(params: dict, progress_callback=None, cancellation_token=None) -> None:
    progress_callback = progress_callback or (lambda percent, step=None: None)

    # ... hacer trabajo ...
    progress_callback(10, "Conectando")
    if cancellation_token:
        cancellation_token.raise_if_cancelled()

    # ... más trabajo ...
    progress_callback(100, "Completado")
Reglas:

Los 3 parámetros deben tener default (None) para no romper invocaciones directas fuera del orquestador.
Los checkpoints de cancelación (cancellation_token.raise_if_cancelled()) van solo en la función de negocio registrada, nunca en submódulos internos de lógica (integrations/<provider>/functions/*.py o equivalentes).
Ponlos después de cada paso costoso: conectar, consultar, procesar, generar archivo, enviar correo.
Un error inesperado (excepción no capturada) lo recoge orchestrator/core/runner.py, que marca la ejecución como ERROR y guarda error_message — no hace falta capturarlo a mano dentro del proceso salvo que quieras un mensaje más específico.

Paso 2 — Registra el callable en orchestrator/registrations.py
Todos los procesos en segundo plano se registran en ese único archivo; las
apps de negocio no se autorregistran en su propio `AppConfig.ready()`.
Agrega el import de la función pública (`<app>/functions/...`) y su
`register_process`:
# orchestrator/registrations.py
from orchestrator.core.registry import register_process
from orders.functions.import_falabella_orders import import_falabella_orders

register_process("orders.import_falabella", import_falabella_orders)
Límite: `registrations.py` es el único módulo de `orchestrator` que importa
código de negocio, y solo la función registrada — nunca modelos ni
submódulos internos de la app dueña (ver docs/apps/orchestrator.md).
`OrchestratorConfig.ready()` importa `registrations.py` al arrancar Django,
solo cuando `RUN_MAIN=true` (proceso hijo de `runserver`) o
`ORCHESTRATOR_FORCE_READY` está definido.
Paso 3 — Crea el ProcessType en base de datos
Vía /admin/orchestrator/processtype/ o por shell/migración de datos:
from orchestrator.models import ProcessType

ProcessType.objects.create(
    code="orders.import_falabella",   # debe coincidir EXACTO con el register_process
    name="Importar pedidos Falabella a Shopify",
    app_label="orders",
    allow_concurrent=False,          # True si se permite correr 2+ al mismo tiempo
    max_concurrent_global=None,      # override opcional, None = usa el límite global
    is_active=True,
)
Con esto el proceso ya queda disponible para lanzarse manual o programado.

3. Cómo lanzar un proceso desde un endpoint (ejecución manual)
No importes ProcessExecution ni concurrency_manager directamente. Usa siempre la función pública launch_process.
# orders/apis.py
from rest_framework.views import APIView
from rest_framework.response import Response

from accounts.permissions import RoleRequiredMixin
from orchestrator.services import launch_process


class ImportarPedidosFalabellaView(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Operaciones"]

    def get(self, request):
        execution = launch_process(
            code="orders.import_falabella",
            user=request.user,
            params={},  # o {"created_after": ..., "created_before": ...} si el proceso lo necesita
        )
        return Response(status=202, data={"execution_id": execution.pk})
Qué hace launch_process por dentro:

Busca el ProcessType por code (falla si no existe o is_active=False).
Si allow_concurrent=False y ya hay una ejecución EJECUTANDO/EN_COLA/CANCELANDO de ese mismo code → lanza ProcessAlreadyRunningError (409).
Crea la fila ProcessExecution (PENDIENTE).
La entrega a concurrency_manager.submit() → arranca de inmediato en un hilo, o queda EN_COLA si no hay cupo.
Devuelve el objeto execution (usa execution.pk para responder al front).

El endpoint responde 202 Accepted de inmediato — el front debe hacer polling sobre GET /api/orchestrator/executions/<id>/ (ver sección 5).
Cancelar una ejecución en curso, desde código (no HTTP)
from orchestrator.services import request_cancellation

execution = request_cancellation(execution)  # marca CANCELANDO + cancellation_requested=True
Normalmente esto se dispara vía el endpoint HTTP (POST /executions/<id>/cancel/), no manualmente desde otro código.

4. Cómo registrar una ejecución programada (scheduler)
Requiere que el ProcessType ya exista (ver sección 2, paso 3). Se registra creando un ProcessScheduleConfig.
Opción recomendada: vía API (aplica en caliente, sin reiniciar el servidor)
POST /api/orchestrator/schedules/
Body según el tipo de programación (schedule_kind):



schedule_kind
Campos obligatorios
Ejemplo de uso




ONCE_DATE
run_at_date, run_at_time
Una sola vez, fecha y hora exactas


DAILY
run_at_time
Todos los días a la misma hora


WEEKLY
weekday (0=Lunes … 6=Domingo), run_at_time
Un día fijo de la semana


MONTHLY
day_of_month, run_at_time
Un día fijo del mes


CRON
cron_expression
Máxima flexibilidad (sintaxis crontab estándar)



Ejemplo — todos los días a las 6:00 am:
{
  "process_type": 1,
  "schedule_kind": "DAILY",
  "run_at_time": "06:00:00",
  "params": {},
  "is_active": true
}
Ejemplo — expresión CRON (cada hora en horario laboral, lunes a viernes):
{
  "process_type": 1,
  "schedule_kind": "CRON",
  "cron_expression": "0 8-18 * * 1-5",
  "params": {},
  "is_active": true
}
Opción alternativa: vía /admin/
Funciona, pero el job no se registra en caliente en el scheduler (register_schedule() solo se dispara desde el ViewSet de la API, en perform_create/perform_update). Si lo creas desde el admin, la programación queda guardada en BD pero solo se activará en el próximo reinicio del servidor (start_scheduler() recarga todo lo is_active=True al arrancar).
Qué pasa cuando llega la hora
APScheduler calcula el trigger → duerme hasta ese instante exacto
   ↓
_run_schedule(schedule_id)
   → busca el ProcessScheduleConfig en BD (is_active=True)
   → launch_process(code=..., user=None, params=schedule.params, triggered_by_schedule=schedule)
        → mismo camino que un lanzamiento manual (valida duplicados, cola, concurrency_manager)

user queda None (nadie hizo clic).
triggered_by_schedule queda apuntando a la programación — así se distingue en el histórico si una ejecución fue manual o automática.
Si a esa hora ya hay una ejecución activa del mismo process_type sin allow_concurrent, launch_process lanza ProcessAlreadyRunningError, que se pierde silenciosamente (nadie espera una respuesta HTTP en ese contexto) — no se genera una nueva fila ni se reintenta.

Editar o desactivar una programación
PATCH /api/orchestrator/schedules/{id}/
{ "is_active": false }
Esto re-registra el job en el scheduler en caliente (quita el job si estaba activo, y no lo vuelve a agregar si is_active=False).
DELETE /api/orchestrator/schedules/{id}/
Quita el job del scheduler y borra el registro de BD.

5. Documentación de endpoints
Prefijo base: /api/orchestrator/ (montado en config/urls.py; el endpoint de
lanzamiento dedicado, si existe, vive en la app dueña del proceso, ej.
/api/orders/falabella/importar/).
Todos requieren sesión de Django autenticada (cookie) y el mixin
RoleRequiredMixin (rol Admin u Operaciones), salvo que se indique lo
contrario. Ver docs/patterns/AUTHORIZATION.md.
5.1. Catálogo de procesos
GET /api/orchestrator/process-types/
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]
Respuesta 200:
[
  {
    "id": 1,
    "code": "orders.import_falabella",
    "name": "Importar pedidos Falabella a Shopify",
    "app_label": "orders",
    "allow_concurrent": false,
    "max_concurrent_global": null,
    "is_active": true
  }
]
5.2. Lanzar un proceso (genérico, vía código)
POST /api/orchestrator/process-types/{code}/launch/
Body: { "params": { ... } }   (opcional)
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]

Nota: para procesos con endpoint propio en su app dueña (ej. un futuro
GET /api/orders/falabella/importar/), ese endpoint normalmente se prefiere
porque puede declarar un permiso más específico que el genérico de
orchestrator. Este endpoint genérico es una vía alterna útil cuando no
existe un endpoint dedicado.

Respuesta 202:
{ "execution_id": 123, "status": "PENDIENTE" }
Respuesta 409 (duplicado, si allow_concurrent=False y ya hay una ejecución activa):
{ "detail": "Ya existe una ejecución en curso para este proceso." }
5.3. Listar ejecuciones
GET /api/orchestrator/executions/
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]
Filtros (query params, combinables):

?status=EJECUTANDO
?process_type=orders.import_falabella

Respuesta 200 (lista de objetos, ver estructura completa en 5.4).
5.4. Detalle de una ejecución (polling)
GET /api/orchestrator/executions/{id}/
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]
Respuesta 200:
{
  "id": 123,
  "process_type": 1,
  "process_type_code": "orders.import_falabella",
  "user": 7,
  "status": "EJECUTANDO",
  "started_at": "2026-09-14T15:00:00Z",
  "finished_at": null,
  "progress_percent": 40,
  "current_step": "Creando pedido en Shopify",
  "error_message": null,
  "duration_seconds": null,
  "cancellation_requested": false,
  "triggered_by_schedule": null,
  "params": {},
  "created_at": "2026-09-14T14:59:50Z"
}
5.5. Cancelar una ejecución
POST /api/orchestrator/executions/{id}/cancel/
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]
Sin body. Respuesta 200: mismo objeto de la ejecución, con status: "CANCELANDO" y cancellation_requested: true. La cancelación es cooperativa — el estado pasa a CANCELADO recién cuando el proceso alcanza su próximo checkpoint interno, no de inmediato.
⚠️ No hay granularidad por process_type hoy: cualquiera con rol Admin u Operaciones puede cancelar ejecuciones de cualquier proceso registrado, no solo las propias ni las de un proceso específico.
5.6. CRUD de programaciones
GET    /api/orchestrator/schedules/
POST   /api/orchestrator/schedules/
GET    /api/orchestrator/schedules/{id}/
PATCH  /api/orchestrator/schedules/{id}/
DELETE /api/orchestrator/schedules/{id}/
Permiso: RoleRequiredMixin, allowed_roles = ["Admin", "Operaciones"]
Campos del payload — ver tabla completa en la sección 4. created_by y created_at son de solo lectura (los completa el backend con request.user).

6. Estados de una ejecución



status
Significado
¿Requiere seguir el polling?




PENDIENTE
Creada, aún no evaluada por la cola
Sí


EN_COLA
Esperando cupo de concurrencia
Sí


EJECUTANDO
Corriendo — usar progress_percent/current_step
Sí


CANCELANDO
Se pidió cancelar, esperando el próximo checkpoint del proceso
Sí


COMPLETADO
Terminó bien
No (estado final)


ERROR
Falló — ver error_message
No (estado final)


CANCELADO
Se canceló efectivamente
No (estado final)


INTERRUMPIDO
El servidor se reinició mientras corría/estaba en cola/cancelando
No (estado final)




7. Errores comunes y troubleshooting



Síntoma
Causa probable
Solución




ProcessType.DoesNotExist al lanzar
No se creó el ProcessType en BD, o code no coincide exacto
Crear el registro (sección 2, paso 3); revisar mayúsculas/puntos en el code


La ejecución falla porque no encuentra la función del proceso
Falta el register_process en orchestrator/registrations.py, el code no coincide con el ProcessType, o el servidor arrancó sin RUN_MAIN=true ni ORCHESTRATOR_FORCE_READY (registrations.py no se cargó)
Agregar el registro en orchestrator/registrations.py con el code exacto; en despliegue, definir ORCHESTRATOR_FORCE_READY


409 ProcessAlreadyRunningError
Ya hay una ejecución activa y allow_concurrent=False
Esperar a que termine, o consultar ?process_type=<code>&status=EJECUTANDO para encontrar la ejecución activa


403 en cualquier endpoint del orquestador
El usuario no tiene sesión o no pertenece al grupo Admin/Operaciones
Agregar el usuario al grupo Django correspondiente (ver docs/patterns/AUTHORIZATION.md), o usar un superusuario para pruebas


Ejecución queda "pegada" en CANCELANDO para siempre
El proceso de negocio nunca llama cancellation_token.raise_if_cancelled() en ningún checkpoint
Agregar checkpoints en la función registrada (ver sección 2, paso 1)


Tras reiniciar el servidor, ejecuciones quedan huérfanas en EJECUTANDO/CANCELANDO/EN_COLA
Estado en memoria (hilos, cola) se pierde al reiniciar
orchestrator/core/recovery.py::recover_orphan_executions() las marca INTERRUMPIDO automáticamente al arrancar (solo corre si RUN_MAIN=true, es decir dentro del proceso hijo del autoreloader de runserver, o con ORCHESTRATOR_FORCE_READY=1)


Programación creada desde /admin/ no se dispara
register_schedule() no se llama automáticamente desde el admin, solo desde la API
Reiniciar el servidor (recarga todo is_active=True), o crear/editar la programación vía API en su lugar


database is locked (SQLite) al cancelar o al finalizar un proceso
SQLite no maneja bien escrituras concurrentes desde varios hilos (petición HTTP + hilo del proceso)
runner.py ya reintenta el guardado vía .update() directo; para desarrollo, considerar activar PRAGMA journal_mode=WAL en settings.py, o usar Postgres local
