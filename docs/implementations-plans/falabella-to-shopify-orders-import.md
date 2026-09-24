# Plan: importar pedidos de Falabella a Shopify (cliente por cédula + tag de origen)

Estado: Fase 0 (`orchestrator`), Fase 1 (cédula del cliente en
`integrations/falabella`) y el reemplazo de la Fase 2 (app `customers`, ver
[`shopify-customers-directory.md`](shopify-customers-directory.md))
implementados y probados — `python manage.py test` completo en verde (107
tests). La Fase 3 se rediseñó y su plan a detalle vive en
[`marketplace-orders-import.md`](marketplace-orders-import.md) — sin
implementar todavía. Este documento no reemplaza el código como evidencia de
estado; antes de ejecutar lo que falta, releer `docs/INDEX.md` y confirmar
que los documentos citados abajo siguen vigentes.

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

## Hallazgo bloqueante (histórico, resuelto en la Fase 0): `orchestrator/` no estaba integrado a este repo

**Ya no aplica — queda documentado solo como registro de lo que se 
encontró y corrigió.** Verificado de nuevo el 2026-09-19 contra el estado
real: `orchestrator` está en `INSTALLED_APPS`, `orchestrator/apps.py`
declara `name = "orchestrator"` (correcto), y son 25 archivos trackeados en
git (`git ls-files orchestrator/`). Si algo de esto no coincide al leer
esto en el futuro, el código manda, no este documento — confirmarlo de
nuevo antes de asumir cualquiera de los dos estados.

Lo que se encontró originalmente (antes de la Fase 0, ver tabla de cambios
abajo):

- `orchestrator/` estaba sin trackear en git (`?? orchestrator/`) y no
  estaba en `INSTALLED_APPS` (`config/settings.py`).
- `orchestrator/apps.py` declaraba `name = "apps.orchestrator"` — no existe
  paquete `apps/` en este repo (el directorio real es top-level
  `orchestrator/`).
- `orchestrator/apis.py` importaba `from apps.users.permissions import
  HasPermissionCode` y usaba `drf_spectacular` — ninguno existe en este
  backend (acá la autorización es `accounts.permissions.RoleRequiredMixin` /
  `ApiKeyRequiredMixin`, y el contrato HTTP se documenta a mano en
  `docs/contracts/API.md`, sin generación automática de OpenAPI).
- `orchestrator/registrations.py` importaba `from
  apps.reports.f11.core.orchestrator import generate_report` — módulo
  inexistente.
- `orchestrator/core/runner.py` importaba `from core_tools.logs import
  report_error` — paquete inexistente.
- `orchestrator/tests/*.py` importaban todo bajo `apps.orchestrator...` y
  `apps.users.models` — inexistentes.
- Faltaba en `requirements.txt`: `APScheduler` (lo usa `core/scheduler.py`)
  y `drf-spectacular` (si se conservaban los decoradores).

El resto (`core/registry.py`, `core/concurrency_manager.py`,
`core/cancellation.py`, `core/recovery.py`, `core/constants.py`,
`models.py`, `serializers.py`) era autocontenido y portable tal cual — bajo
acoplamiento real, no necesitó cambios de fondo.

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

> Actualización posterior: esta decisión se revirtió. Hoy todos los procesos
> se registran de forma centralizada en `orchestrator/registrations.py`
> (ver `docs/apps/orchestrator.md`).

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

## Fase 3 — reemplazada: app nueva `orders`

**Rediseñada el 2026-09-19, sin implementar todavía.** El diseño original
de esta sección (un único modelo `FalabellaOrderImport` como ledger simple)
se reemplazó por un modelo general multi-marketplace
(`MarketplaceOrder`/`MarketplaceOrderItem`), decisión explícita para que un
canal futuro (Mercado Libre, etc.) reutilice la misma estructura. Incluye
también un hallazgo real de esa misma fecha: pedidos reales de Falabella
(`ShippingType: Dropshipping`) llegaron sin email ni teléfono del
comprador, lo que `create_customer()` exige — el diseño nuevo lo maneja
como un estado de error explícito, no lo asume como caso raro.

Plan completo, con modelos, funciones, riesgos y puntos abiertos, en
[`marketplace-orders-import.md`](marketplace-orders-import.md) — ese
documento reemplaza esta fase.

## Documentación a actualizar en el mismo cambio

Ver la sección "Documentación a actualizar" de
[`marketplace-orders-import.md`](marketplace-orders-import.md) — ese
documento tiene la lista vigente para la app `orders`.

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
