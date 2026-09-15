# Plan: importar pedidos de Falabella a Shopify (cliente por cédula + tag de origen)

Estado: Fase 0 (`orchestrator`), Fase 1 (cédula del cliente en
`integrations/falabella`) y el reemplazo de la Fase 2 (app `customers`, ver
[`shopify-customers-directory.md`](shopify-customers-directory.md))
implementados y probados — `python manage.py test` completo en verde (107
tests). Solo falta la Fase 3 (app `orders`). Este documento no reemplaza el
código como evidencia de estado; antes de ejecutar lo que falta, releer
`docs/INDEX.md` y confirmar que los documentos citados abajo siguen
vigentes.

## Alcance y decisiones ya tomadas

- Disparo: vía `orchestrator`, con registro programable por cron.
- Cédula del cliente: vive en el campo "empresa" (`company`) de la dirección
  del cliente en Shopify — confirmado contra datos reales (2 de 10 clientes
  muestreados). **Pero no se puede buscar por ese campo** (ver Fase 2
  abajo): la búsqueda de "¿ya existe este cliente?" se resuelve contra un
  directorio local sincronizado, no contra una búsqueda en vivo a Shopify —
  ver [`shopify-customers-directory.md`](shopify-customers-directory.md).
- `pamo-one-engineering-audit/01-ARQUITECTURA.md` es de un producto hermano
  distinto (`pamo_orders`, con roles de *worker*, multiproceso); no aplica a
  este repo, que hoy es un monolito Django simple con
  `INSTALLED_APPS = [accounts, integrations]`. No se usa como base.

## Hallazgo bloqueante: `orchestrator/` no está integrado a este repo

Confirmado en código, no en documentación:

- `orchestrator/` está sin trackear en git (`?? orchestrator/`) y no está en
  `INSTALLED_APPS` (`config/settings.py`).
- `orchestrator/apps.py` declara `name = "apps.orchestrator"` — no existe
  paquete `apps/` en este repo (el directorio real es top-level
  `orchestrator/`).
- `orchestrator/apis.py` importa `from apps.users.permissions import
  HasPermissionCode` y usa `drf_spectacular` — ninguno existe en este
  backend (acá la autorización es `accounts.permissions.RoleRequiredMixin` /
  `ApiKeyRequiredMixin`, y el contrato HTTP se documenta a mano en
  `docs/contracts/API.md`, sin generación automática de OpenAPI).
- `orchestrator/registrations.py` importa `from
  apps.reports.f11.core.orchestrator import generate_report` — módulo
  inexistente.
- `orchestrator/core/runner.py` importa `from core_tools.logs import
  report_error` — paquete inexistente.
- `orchestrator/tests/*.py` importan todo bajo `apps.orchestrator...` y
  `apps.users.models` — inexistentes.
- Falta en `requirements.txt`: `APScheduler` (lo usa `core/scheduler.py`) y
  `drf-spectacular` (si se conservan los decoradores).

El resto (`core/registry.py`, `core/concurrency_manager.py`,
`core/cancellation.py`, `core/recovery.py`, `core/constants.py`,
`models.py`, `serializers.py`) es autocontenido y portable tal cual — bajo
acoplamiento real, no requiere cambios de fondo.

## Fase 0 — Adaptar `orchestrator` a Pamo (prerrequisito)

| Archivo | Cambio |
| --- | --- |
| `orchestrator/apps.py` | `name = "apps.orchestrator"` → `name = "orchestrator"`. El `app_label` ya resuelve a `"orchestrator"` en ambos casos (Django toma el último segmento), así que las migraciones existentes no se rompen. |
| `orchestrator/apis.py` | Reemplazar `HasPermissionCode`/`required_roles_by_app` por `RoleRequiredMixin` + `allowed_roles` (patrón de `docs/patterns/AUTHORIZATION.md`). Quitar los decoradores `@extend_schema*` y el import de `drf_spectacular` (evita una dependencia nueva que el resto del proyecto no usa; el contrato se documenta a mano en `docs/contracts/API.md` igual que el resto de la API). |
| `orchestrator/registrations.py` | Vaciar el import a `apps.reports.f11` (no existe). Que `orchestrator` importe módulos de una app de negocio viola el mismo límite que ya aplica a `integrations` ("un área no importa modelos internos del proveedor", en sentido inverso). Mejor: cada app de negocio se autorregistra en su propio `AppConfig.ready()` llamando a `orchestrator.core.registry.register_process(...)`. `orders` lo hará así en la Fase 3. |
| `orchestrator/core/runner.py` | Quitar `from core_tools.logs import report_error`; sustituir por `logging.getLogger(__name__).exception(...)`. Revisar si el `except SystemExit` sigue teniendo sentido sin ese paquete (ningún proceso de este repo hará `sys.exit()`) — se puede simplificar, pero dejarlo no hace daño. |
| `orchestrator/tests/*.py` | Reescribir imports `apps.orchestrator.*` → `orchestrator.*`; reemplazar `apps.users.models.Role/UserRole` por `django.contrib.auth.models.Group` (igual que `accounts`). |
| `requirements.txt` | Agregar `APScheduler` (necesario para el scheduler CRON). No agregar `drf-spectacular` si se quitan los decoradores. |
| `config/settings.py` | Agregar `'orchestrator'` a `INSTALLED_APPS`. |
| `config/urls.py` | Montar `path('api/orchestrator/', include('orchestrator.urls'))`. |
| `orchestrator/migrations/0001_initial.py` | No requiere cambios (usa `settings.AUTH_USER_MODEL`, que en este repo es el `User` por defecto). Ejecutar `makemigrations --check` igual para confirmar. |

**Hecho.** Verificado con `python manage.py check`,
`makemigrations --check --dry-run`, `python manage.py test` (73 tests en
verde, incluidos los 10 de `orchestrator/tests/`), y arranque real del
servidor de desarrollo (`GET /api/orchestrator/process-types/` responde 403
sin sesión, como se espera).

Un cambio de diseño respecto al plan original: `orchestrator/registrations.py`
se eliminó en vez de vaciarse — cada app de negocio se autorregistra en su
propio `AppConfig.ready()` (ver `docs/apps/orchestrator.md`), así
`orchestrator` nunca importa una app de negocio.

Documentación creada/actualizada: `docs/apps/orchestrator.md` (expediente
nuevo), `docs/architecture/APP_BOUNDARIES.md` y `docs/INDEX.md` (fila nueva),
`docs/patterns/orchestrator-usage-guide.md` (rutas, permisos y ejemplos
corregidos a las convenciones reales de este repo).

## Fase 1 — `integrations/falabella`: exponer la cédula del cliente

**Hecho.** Confirmado contra una llamada real de solo lectura a `GetOrders`
el 2026-09-15 (5 pedidos reales, rango de 180 días): no hace falta una acción
`GetOrder` singular — el campo de cédula ya viene en `GetOrders`, a nivel de
la orden (no dentro de `AddressBilling`), como `NationalRegistrationNumber`
(8-10 dígitos, presente en los 5 pedidos consultados). `CustomerFirstName`/
`CustomerLastName` también van a nivel de la orden, y `CustomerEmail` dentro
de `AddressBilling`.

`_normalize_order()` en `get_orders.py` se amplió (no se duplicó la función)
para devolver además `customer_first_name`, `customer_last_name`,
`customer_identification` y `customer_email` — con fallback a `""` si algún
pedido no trae el campo (queda a criterio de quien orquesta la importación,
Fase 3, decidir qué hacer con un pedido incompleto). Comentario desactualizado
de `config/constants.py` ("credenciales de Falabella pendientes de Railway")
corregido — las credenciales ya estaban configuradas.

Pruebas agregadas en `integrations/falabella/tests.py` con datos sintéticos
(nunca la PII real de los pedidos consultados, que no se guardó en ningún
archivo del repo): normalización de los campos nuevos y fallback a cadena
vacía cuando faltan. Suite completa verificada en verde (74 tests).

## Fase 2 — reemplazada: `integrations/shopify` buscar/crear cliente directo

**Investigada en vivo el 2026-09-15 y descartada tal como estaba planteada.**
Se hicieron llamadas reales de solo lectura (introspección + muestra de
clientes reales) contra la tienda, sin escribir nada, y se encontraron dos
problemas de fondo:

1. `CustomerInput` (el input de `customerCreate`) **no tiene ningún campo de
   dirección** — confirmado por introspección. La cédula solo se puede
   escribir con una segunda llamada (`customerAddressCreate` o
   `customerAddressUpdate`, según si el cliente ya tiene dirección).
2. `customers(query: "company:<valor>")` **no filtra nada** — se comparó
   contra `customers(query: "company:valor-inventado-que-no-existe")` y
   devolvió exactamente la misma lista sin filtrar (mismos IDs, mismo
   orden). Se probó con y sin comillas, con y sin prefijo
   `default_address.` — siempre igual. En cambio `email:<valor inventado>`
   sí devuelve 0 resultados correctamente, confirmando que el mecanismo de
   búsqueda en sí funciona; `company` simplemente no es un campo indexado
   para búsqueda de clientes en esta API.

Buscar un cliente por cédula contra Shopify en vivo, en cada importación de
pedido, **no es viable**. La solución (directorio local sincronizado con
Shopify, incluye el método para escribir/actualizar `company` en un cliente
existente) queda detallada en
[`shopify-customers-directory.md`](shopify-customers-directory.md) — ese
documento reemplaza esta fase.

`create_order()` (`integrations/shopify/functions/create_order.py`) **ya
acepta `tags`** — no requiere cambios. Solo hay que pasar el tag de origen
(ej. `["falabella"]`) desde el orquestador de negocio; esto no se vio
afectado por el hallazgo de arriba.

## Fase 3 — App nueva `orders`

Primera app de negocio "real" de este repo aparte de `accounts` (hoy
`accounting`/`facturacion`/`logistics` son carpetas vacías, sin
`INSTALLED_APPS`, sin código fuente — solo "Pendiente" en `docs/INDEX.md`).
Estructura:

- `orders/models.py` — `FalabellaOrderImport`: `falabella_order_id` (único),
  `falabella_order_number`, `customer_identification`,
  `shopify_customer_id`, `shopify_order_id` (null hasta confirmarse),
  `status` (pending/imported/failed), `error_message`, `created_at`,
  `imported_at`. Es el dato de negocio (ledger de idempotencia) que le
  pertenece a `orders`, no a `integrations` — evita reimportar el mismo
  pedido si `orderCreate` no tiene clave de idempotencia propia (ya
  documentado como riesgo en `create_order.py`).
- `orders/functions/import_falabella_order.py` — por cada pedido:
  `get_order_items` → resolver variantes (`get_variant_by_sku`) →
  `customers.find_by_identification` (lookup **local**, ver
  [`shopify-customers-directory.md`](shopify-customers-directory.md) — ya
  no se busca contra Shopify en vivo) → si no existe, `create_customer` +
  `create_customer_address` (Shopify) y upsert local inmediato →
  `create_order(..., tags=["falabella"])` → registrar en
  `FalabellaOrderImport`. Firma compatible con el contrato del orchestrator:
  `(params, progress_callback=None, cancellation_token=None)`.
- Depende de que la app `customers` (ver documento enlazado) exista primero
  — es un prerrequisito de esta fase, igual que `orchestrator` lo fue de la
  Fase 1.
- `orders/apps.py::ready()` — `register_process("orders.import_falabella",
  import_falabella_orders)` (autorregistro, sin que `orchestrator` conozca a
  `orders`).
- Alta del `ProcessType` (`code="orders.import_falabella"`) — vía admin o
  data migration.
- `orders/admin.py` — registrar `FalabellaOrderImport` (visibilidad/soporte).
- `orders/tests.py` — camino feliz (cliente nuevo, cliente existente) y
  rechazo de permisos si se agrega un endpoint propio.
- Endpoint propio: no es obligatorio si el disparo es 100% vía
  `orchestrator` (launch genérico `POST
  /api/orchestrator/process-types/orders.import_falabella/launch/` +
  programación CRON vía `/api/orchestrator/schedules/`). Si luego se quiere
  un endpoint dedicado (`GET /api/orders/falabella/importar/`), sigue el
  mismo patrón documentado en la guía del orquestador (llama a
  `launch_process` con permiso propio).
- `INSTALLED_APPS`: agregar `'orders'`.

## Documentación a actualizar en el mismo cambio

- `docs/architecture/APP_BOUNDARIES.md`: agregar fila `orders` (área de
  negocio, dueña del ledger de importación) — `orchestrator` y `customers`
  ya se documentan en sus propios cambios (ver documentos enlazados).
- `docs/apps/orders.md`: expediente nuevo.
- `docs/architecture/INTEGRATIONS.md` y `docs/apps/integrations.md`:
  agregar las funciones nuevas de Falabella a la tabla de proveedores (las
  de Shopify se documentan como parte de `shopify-customers-directory.md`).
- `docs/contracts/API.md`: si se agrega el endpoint dedicado de `orders`,
  documentarlo; si no, documentar solo el uso de `orders.import_falabella`
  sobre el contrato ya existente del orquestador.
- `docs/INDEX.md`: fila nueva para `orders`.

## Puntos abiertos que exigen verificación en vivo antes de programar

1. ~~Nombre exacto del campo con la cédula en Falabella~~ — resuelto en Fase 1:
   `NationalRegistrationNumber` a nivel de la orden, confirmado contra 5
   pedidos reales el 2026-09-15.
2. ~~Sintaxis exacta de búsqueda `customers(query: "company:...")`~~ —
   resuelto: **no funciona, confirmado contra la tienda real** (ver "Fase 2
   — reemplazada" arriba). Reemplazado por un directorio local; puntos
   abiertos de ese enfoque están en
   [`shopify-customers-directory.md`](shopify-customers-directory.md).
3. ~~Campos válidos de `CustomerInput` para `customerCreate` con `company`~~
   — resuelto: `CustomerInput` no tiene campo de dirección, confirmado por
   introspección. `company` se escribe con `customerAddressCreate`/
   `customerAddressUpdate` (`MailingAddressInput`), documentado en
   `shopify-customers-directory.md`.
4. ~~Credenciales reales de Falabella~~ — resuelto: ya estaban configuradas
   (confirmado al hacer la llamada real de la Fase 1).
5. Valor exacto del tag de origen en la orden (`"falabella"` vs algo como
   `"origen:falabella"`) — asunción razonable, ajustable sin impacto
   arquitectónico. (No confundir con el tag que se había considerado para
   buscar clientes — esa idea se descartó, ver Fase 2.)
