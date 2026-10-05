# Plan: copia local de los pedidos de Shopify

Estado: **implementado (2026-10-05)**, pendiente de despliegue (ver
"Despliegue"). Reemplaza la fuente de datos de
[`shopify-orders-listing.md`](shopify-orders-listing.md): `GET /api/orders/`
deja de consultar Shopify en cada petición. El usuario pidió implementar el
plan tal cual, así que los supuestos (paginación por página, reconciliación
cada 30 min) se tomaron como confirmados. Lo que cambió respecto al diseño
está en "Notas de implementación".

## Contexto

Hoy `OrderListAPI` llama a Shopify en cada petición del frontend
(`list_shopify_orders` → `integrations.shopify.list_orders_page`). Si
Shopify está lento o caído, el listado también, y cada filtro gasta una
consulta del proveedor. El usuario no quiere esa dependencia.

`customers` ya resolvió el mismo problema para los clientes: una tabla
local sincronizada con Shopify por webhook más una reconciliación
programada ([`../apps/customers.md`](../apps/customers.md)). Este plan aplica
el mismo patrón a los pedidos, dentro de `orders`.

## Objetivo

1. Modelo local de pedidos de Shopify en `orders`, con los datos ya
   acordados: pedido, cliente, líneas, marketplace y totales.
2. Carga inicial: los pedidos del último mes, con la "fecha desde" como
   parámetro.
3. Webhooks de Shopify para crear, actualizar y eliminar pedidos.
4. Un proceso del orquestador que recupere lo que los webhooks no
   entregaron.
5. `GET /api/orders/` lee solo la base de datos del proyecto.

## Verificado contra la tienda real (solo lectura, 2026-10-05)

- `Order` tiene `updatedAt`, `cancelledAt` y `closedAt`, y `orders` acepta
  `sortKey: UPDATED_AT`.
- Temas de webhook disponibles: `ORDERS_CREATE`, `ORDERS_UPDATED`,
  `ORDERS_DELETE` (además de `ORDERS_CANCELLED`, `ORDERS_PAID`, etc., que
  `ORDERS_UPDATED` ya cubre).
- La consulta `webhookSubscriptions` de la app no devolvió ninguna
  suscripción. **Eso no prueba que la tienda no tenga webhooks**: la API
  solo lista los que creó la propia app, no los creados desde el admin
  (corregido el 2026-10-05; ver "Notas de implementación").
- `SHOPIFY_WEBHOOK_SECRET` ya tiene valor en el entorno local.
- El último mes tiene más de 250 pedidos. La carga inicial necesita varias
  páginas.

## Decisiones

| Tema | Decisión |
| --- | --- |
| Dónde vive | `orders`, dueña de los pedidos ([`../architecture/APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md)). No es una app nueva. `integrations/shopify` solo agrega lectura y transporte. |
| Qué trae el webhook | **Solo el id del pedido.** El proceso vuelve a leer el pedido de Shopify por GraphQL y lo guarda. Así hay un único normalizador (el de GraphQL, ya usado por `list_orders_page`), los avisos que llegan desordenados no pisan datos nuevos con viejos, y un aviso repetido no hace daño. Cuesta una lectura por aviso. `customers` sí parsea el payload REST; aquí no se copia eso, porque el pedido tiene muchos más campos y habría dos normalizadores. |
| Eliminación | **Borrado lógico** (`deleted_at`). Un `orders/updated` que llegue después del `orders/delete` no revive el pedido. El listado excluye los borrados. |
| Recuperación | Por **fecha de actualización** (`updated_at`), no por cursor: un cursor de Shopify solo vale para la misma búsqueda y el mismo orden, y no sirve como marca de "desde dónde seguir" entre corridas. La recuperación guarda su propio checkpoint (ver abajo). |
| Cancelados | Se guardan con `cancelled_at` y se listan. El frontend decide cómo mostrarlos. |
| Marketplace | Se calcula al guardar, desde las etiquetas (sin distinguir mayúsculas), como hoy. Al listar, la fila de `MarketplaceOrder` sigue mandando para el comprador, los números del marketplace y la bodega. |
| Paginación del listado | **Supuesto:** por número de página con `count` (`PageNumberPagination`, como `products`), ya que la fuente es local. Cambia el contrato publicado (`cursor` → `page`). |
| Programación | **Supuesto:** recuperación cada 30 minutos, con 15 minutos de solape. |

## Modelo (`orders/models.py`)

`ShopifyOrder`:

- `shopify_id` (único), `name`, `shopify_created_at` (indexado),
  `shopify_updated_at`, `cancelled_at`, `deleted_at` (nulos);
- `financial_status`, `fulfillment_status`, `tags` (`JSONField`),
  `marketplace` (indexado; valores de `Marketplace` o `shopify`);
- `email`, `phone`, `total` (`DecimalField`), `currency`;
- cliente de Shopify: `customer_id`, `customer_first_name`,
  `customer_last_name`, `customer_identification`, `customer_city`,
  `customer_region`, `customer_address`;
- `synced_at` (`auto_now`).

`ShopifyOrderLine` (FK `order`, `related_name="lines"`): `sku`, `name`,
`quantity`, `unit_price` y `line_total` (`DecimalField`), `position`.

Con el modelo local ya no hay `line_items_truncated`: se guardan todas las
líneas (ver "Integración").

`ShopifyOrderSyncState` (una sola fila): `last_reconciled_at`, la hora en que
**empezó** la última recuperación que terminó bien. No se usa el máximo de
`shopify_updated_at` de la tabla: los webhooks lo adelantan, y un aviso
perdido anterior quedaría fuera.

Sin FK a `MarketplaceOrder`: se cruza por `shopify_order_id`, que ya está
indexado.

## Integración (`integrations/shopify/`)

- Agregar `updatedAt` y `cancelledAt` a `LIST_ORDERS_PAGE`, y los
  parámetros `sortKey` y `reverse` como variables (la carga y la
  recuperación ordenan por `UPDATED_AT` ascendente).
- `list_orders_page(..., sort_key="CREATED_AT", reverse=True)`: agregar esos
  dos parámetros sin cambiar el comportamiento por defecto.
- Nueva consulta `GET_ORDER` y función `get_order(order_id) → pedido
  normalizado | None` (`None` si Shopify no lo encuentra). Mismos campos y
  **el mismo normalizador** que `list_orders_page` (extraer
  `_normalize_order` a un lugar compartido del módulo). Recorre todas las
  líneas (`lineItems(first: 100, after:)`), sin el tope de 30.
- Registrar todo en `QUERIES.md`, verificando por introspección.

## Negocio (`orders/functions/`)

- `upsert_shopify_order(data)` (`upsert_shopify_order.py`): **punto único
  de "cómo se guarda un pedido"**, usado por el webhook, la carga inicial y
  la recuperación.
  - Va en `transaction.atomic()` con `select_for_update` sobre la fila
    existente. Si al crear salta un `IntegrityError` porque dos avisos
    llegaron a la vez, reintenta una vez.
  - Si la fila tiene `deleted_at`, no hace nada.
  - Si `data.updated_at` es anterior al guardado, no hace nada.
  - Si no, actualiza la cabecera y **reemplaza** las líneas.
  - Calcula `marketplace` desde las etiquetas (mover aquí `_marketplace`
    de `list_shopify_orders`).
  - Calcula `line_total` con `order_listing_format.format_line`.
  - Si un pedido trae `line_items_truncated`, lo vuelve a leer con
    `get_order` antes de guardar.
- `mark_shopify_order_deleted(shopify_id)`: pone `deleted_at`. Si la fila
  no existe, crea una marca borrada mínima para que un `orders/updated`
  tardío no la cree.
- `process_shopify_order_webhook(params, ...)`: proceso
  `orders.process_shopify_order_webhook`, con `allow_concurrent=True`.
  `params = {"topic", "order_id"}`.
  - Con `orders/delete`, llama a `mark_shopify_order_deleted`.
  - Con cualquier otro tema, lee con `get_order` y luego guarda con
    `upsert_shopify_order`.
  - Si `get_order` devuelve `None`, trata el pedido como borrado.
- `sync_shopify_orders(query, sort_key, ...)`: bucle compartido. Recorre
  las páginas de `list_orders_page` (50 por página), guarda cada pedido y
  revisa la cancelación entre páginas, como
  `customers.reconcile_from_shopify`.
- `backfill_shopify_orders(params, ...)`: proceso
  `orders.backfill_shopify_orders`, con `allow_concurrent=False` y ejecución
  manual.
  - `params = {"created_from": "YYYY-MM-DD"}`. Si no viene, usa hoy menos
    30 días (hora de Colombia).
  - Busca `created_at:>=` ordenado por `UPDATED_AT`.
  - Si no hay checkpoint, deja `last_reconciled_at` en la hora de inicio,
    para que la recuperación siga desde ahí.
- `reconcile_shopify_orders(params, ...)`: proceso
  `orders.reconcile_shopify_orders`, con `allow_concurrent=False` y
  programado.
  - Busca `updated_at:>= last_reconciled_at − 15 min`, ordenado por
    `UPDATED_AT` ascendente.
  - Sin checkpoint, usa hoy menos 30 días, así que su primera corrida
    también hace de carga inicial.
  - Guarda la hora de inicio como checkpoint **solo si termina bien**. Si
    se cancela o falla, la próxima corrida repite la ventana.
  - **No detecta borrados**: Shopify no lista los pedidos eliminados. Un
    `orders/delete` perdido deja el pedido visible (ver "Riesgos").
- Registrar los tres procesos en `orchestrator/registrations.py` y sembrar
  sus `ProcessType` con una migración de datos, como
  `0011_seed_sodimac_process_type`.

## Webhook (`orders/webhooks.py`)

`ShopifyOrderWebhookView`, `POST /api/orders/webhooks/shopify/`, siguiendo
[`../patterns/PROVIDER_WEBHOOKS.md`](../patterns/PROVIDER_WEBHOOKS.md) y
`customers/webhooks.py`:

- `AllowAny`. La firma se verifica con
  `ShopifyClient.verify_webhook_signature(request.body, …)` **antes** de leer
  `request.data`. Sin firma válida, responde `403`.
- Lee el tema de `X-Shopify-Topic`. Si no es `orders/create`,
  `orders/updated` u `orders/delete`, o si el body no trae `id`, responde
  `200` sin lanzar nada.
- Si es válido, lanza `orders.process_shopify_order_webhook` con
  `{"topic", "order_id": str(id)}` (no el payload completo) y responde
  `200`. Shopify espera la respuesta en menos de 5 s.

Suscripción: desde el admin de Shopify (Configuración → Notificaciones → Webhooks), igual
que `customers`, con los tres eventos de pedido, formato JSON y la URL
pública de Railway. No se crea por API (decisión del usuario, 2026-10-05):
así todos los webhooks de la tienda se firman con la misma clave.

## API (`GET /api/orders/`)

- Nueva `orders/functions/list_orders.py`. Lee `ShopifyOrder` con
  `deleted_at` nulo, junto con `prefetch_related("lines")`, y en una sola
  consulta las filas de `MarketplaceOrder` de la página, unidas por
  `shopify_order_id`. Así obtiene el comprador real, los números del
  marketplace y la bodega, igual que hoy.
- Filtros en la base de datos:
  - `marketplace`: por el campo.
  - Fechas: por `shopify_created_at`, en días de Colombia.
  - `search`: `name` exacto (sin `#`), o un `marketplace_order_number` de
    `MarketplaceOrder` (subconsulta por `shopify_order_id`).
- Respuesta: `count`, `next`, `previous`, `orders` (misma forma que hoy,
  más `cancelled_at`), `orders_not_created` y `orders_not_created_count`
  (sin cambios). Se agrega `last_synced_at` (`last_reconciled_at`) para que
  el frontend muestre qué tan fresca está la información.
- Desaparecen `cursor`, `next_cursor`, `has_next_page`, el `502` y
  `line_items_truncated`. `page_size` sigue con un tope de 50.
- Se elimina `list_shopify_orders.py`. Su `build_orders_query` y sus
  pruebas ya no aplican. `_marketplace` se mueve a la función que guarda.
  `list_orders_page` se conserva, porque lo usa la sincronización.

## Reutilización

- Patrón completo de `customers`: webhook firmado, proceso del orquestador,
  función única de guardado y reconciliación paginada con cancelación.
- `ShopifyClient.verify_webhook_signature`, `ShopifyClient.request_graphql`
  y `list_orders_page`.
- `order_listing_format`: importes, comprador local, bodega y rango de
  días.
- `RoleRequiredMixin` y `ORDERS_LIST_ROLES`, sin cambios.

## Riesgos y vacíos

1. **Secreto de firma**: los webhooks creados desde el admin se firman
   con la clave que muestra la pantalla de webhooks.
   `SHOPIFY_WEBHOOK_SECRET` debe ser esa clave, la misma que usa
   `customers`. Se comprueba con una entrega real: si responde `403`, el
   secreto no coincide.
2. **Borrados perdidos**: la recuperación no ve pedidos eliminados. Si se
   pierde un `orders/delete`, el pedido sigue en la lista. Es poco
   frecuente (en Shopify se archiva más de lo que se elimina). Si llega a
   importar, un proceso semanal puede comparar los ids locales recientes
   contra Shopify.
3. **Varios workers**: el orquestador es por proceso
   ([`../apps/orchestrator.md`](../apps/orchestrator.md)). Los avisos
   concurrentes del mismo pedido los resuelve el bloqueo de fila del
   guardado, no el orquestador.
4. **Volumen de avisos**: `orders/updated` se dispara con cada cambio
   (pago, despacho, etiqueta), y cada aviso es una lectura de unos 10
   puntos de costo en Shopify. Con el volumen actual (unos 20 pedidos al
   día) es despreciable.
5. **Contrato**: pasar de cursor a página cambia lo que ya está publicado.
   Coordinarlo con el frontend.
6. **Pruebas del orquestador que ya fallaban**: `GetOrdersTest` usa el
   nombre de ruta `orchestrator-process-types`. Este plan no la toca.

## Pruebas

Shopify simulado y sin red.

- `integrations/shopify`:
  - `get_order`: normaliza, recorre todas las líneas y devuelve `None` si
    el pedido no existe.
  - `list_orders_page` con `sort_key`.
- `upsert_shopify_order`:
  - crea, actualiza y reemplaza las líneas;
  - ignora un dato más viejo;
  - no revive un pedido borrado;
  - etiqueta en mayúsculas;
  - pedido truncado que se vuelve a leer;
  - reintento ante `IntegrityError`.
- Webhook:
  - firma válida lanza el proceso con el tema y el id;
  - firma inválida o ausente responde `403`;
  - tema desconocido o sin `id` responde `200` sin lanzar nada;
  - nunca pasa el payload completo.
- Proceso del webhook:
  - crea o actualiza;
  - `orders/delete` marca el borrado;
  - `get_order` con `None` cuenta como borrado.
- Carga inicial y recuperación:
  - ventana por defecto y parámetro `created_from`;
  - checkpoint con solape;
  - el checkpoint solo avanza si la corrida termina bien;
  - cancelación entre páginas;
  - sin checkpoint, ventana de 30 días;
  - `ProcessType` sembrados.
- API:
  - lee solo de la base: falla si se llama a `list_orders_page`;
  - cada filtro y la paginación con `count`;
  - excluye los borrados;
  - comprador real, bodega y números desde `MarketplaceOrder`;
  - `orders_not_created` sin cambios;
  - `403` sin sesión o sin rol;
  - `400` por parámetro inválido;
  - número de consultas acotado (`assertNumQueries`).
- `python manage.py check` y `makemigrations --check`.

## Despliegue (orden)

1. Migrar.
2. Correr `orders.backfill_shopify_orders` (los últimos 30 días).
3. Programar `orders.reconcile_shopify_orders` cada 30 minutos por la API
   del orquestador.
4. Crear los tres webhooks de pedidos desde el admin de Shopify con la
   URL pública.
5. Confirmar una entrega real firmada (riesgo 1).
6. Publicar el nuevo contrato al frontend.

## Documentación a actualizar en el mismo cambio

- [`../apps/orders.md`](../apps/orders.md): sección "Copia local de pedidos
  de Shopify" y reescritura de "Listado para el frontend".
- [`../contracts/API.md`](../contracts/API.md): `GET /api/orders/`
  (paginación, `cancelled_at`, `last_synced_at`) y
  `POST /api/orders/webhooks/shopify/`.
- [`../apps/integrations.md`](../apps/integrations.md) y
  `integrations/shopify/QUERIES.md`: `get_order` y `sort_key`.
- [`../apps/orchestrator.md`](../apps/orchestrator.md): los tres
  `register_process`.
- [`../apps/customers.md`](../apps/customers.md): "Puntos abiertos",
  cómo se suscriben los webhooks.
- [`shopify-orders-listing.md`](shopify-orders-listing.md): estado
  "reemplazado en la fuente de datos por este plan".
- [`../INDEX.md`](../INDEX.md) y
  [`../architecture/APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md):
  `orders` mantiene una copia local de los pedidos de Shopify.

## Notas de implementación

Verificado contra la tienda real (solo lectura) el 2026-10-05:

- **Esquema**: `order(id:)` verificado por introspección.
- `list_orders_page` ordenado por `UPDATED_AT` ascendente y `get_order`
  devuelven la misma forma; `get_order` devuelve `None` para un id
  inexistente.
- **Prueba con datos reales** en una base SQLite temporal (no la local ni
  la de Railway):
  - La carga desde el 2026-10-01 trajo 92 pedidos y 108 líneas en 1,1 s,
    sin totales ni precios nulos.
  - Repetirla actualizó los mismos 92 pedidos, sin duplicar.
  - Por marketplace: `shopify` 35, `sodimac` 26, `mercadolibre` 13,
    `falabella` 12 y `madecentro` 6.

Cambios respecto al diseño:

- `ORDER_FIELDS` y `LineItemFields` son fragmentos GraphQL compartidos por
  `LIST_ORDERS_PAGE` y `GET_ORDER`. La normalización vive en
  `integrations/shopify/functions/normalize_order.py`.
- La detección del marketplace pasó a
  `orders/functions/shopify_order_marketplace.py` (`marketplace_from_tags`,
  `SHOPIFY_CHANNEL`, `MARKETPLACE_FILTERS`).
- El bucle compartido es un generador (`iter_shopify_order_pages`), para
  que la cancelación se revise en la función registrada y no en un
  submódulo, como pide la guía del orquestador.
- El listado lee `last_synced_at` sin crear la fila de estado: un GET no
  escribe.
- Las fechas de `orders_not_created` pasaron a hora de Colombia, igual que
  las de `orders`.
- Durante la implementación el usuario quitó `GetOrdersTest` y su ruta
  `test/`. Se quitó el import que quedaba en `orders/urls.py`. Con eso
  vuelven a pasar las tres pruebas del orquestador que fallaban por el
  nombre de ruta duplicado.
- **Suscripción por API eliminada** (2026-10-05, a pedido del usuario): se
  había implementado `ensure_webhook_subscription` y el comando
  `subscribe_shopify_order_webhooks`, pero se quitaron. Los webhooks se crean
  desde el admin, como el de `customers`, para que todos se firmen con la
  misma clave. Mezclar los dos métodos dejaría avisos con otra firma (`403`).
  El hallazgo de "la tienda no tiene suscripciones" se corrigió: la API solo
  lista las de la propia app.
