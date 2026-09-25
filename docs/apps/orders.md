# App `orders`

`orders/` orquesta la importación de pedidos de marketplaces hacia Shopify:
Falabella y Madecentro por lote programado, y Mercado Libre por webhook. No es
transporte de proveedor (eso vive en `integrations/`) y no depende de
`customers/`: los pedidos de cada canal se crean en Shopify a nombre de un
**cliente fijo por canal**, y los datos reales del comprador se guardan
aquí para facturar después en Siigo. Planes en
[`../implementations-plans/marketplace-orders-import.md`](../implementations-plans/marketplace-orders-import.md),
[`../implementations-plans/falabella-fixed-customer.md`](../implementations-plans/falabella-fixed-customer.md)
(cliente fijo, vigente) y
[`../implementations-plans/mercadolibre-orders-import.md`](../implementations-plans/mercadolibre-orders-import.md)
y
[`../implementations-plans/madecentro-orders-import.md`](../implementations-plans/madecentro-orders-import.md).

## Capacidades

- `MarketplaceOrder` / `MarketplaceOrderItem`: modelo general (no
  específico de Falabella) que registra cada pedido de marketplace, sus
  ítems, y el resultado de intentar llevarlo a Shopify. El campo
  `marketplace` (`TextChoices`) es lo que distingue el canal — un
  marketplace nuevo agrega un valor ahí, no una tabla nueva.
- **El indicador de "falta procesar" es `shopify_order_id == ""`** — no hay
  checkpoint de fechas. Un pedido nuevo y uno que quedó en error en una
  corrida anterior se tratan igual: la siguiente corrida los reintenta sin
  lógica aparte.
- Datos del comprador en `MarketplaceOrder` (fuente para la futura factura
  en Siigo): `customer_identification` (cédula), `customer_first_name`,
  `customer_last_name`, `customer_email`, `customer_address`,
  `customer_city`, `customer_region` (departamento) y `customer_phone`, tal
  como los normaliza `integrations.falabella.get_orders` desde
  `AddressBilling`. Falabella no suele mandar email ni teléfono.
- Cliente fijo de Shopify: `FALABELLA_SHOPIFY_CUSTOMER_ID`
  (`config/constants.py`). Sin ese valor, `process_pending_orders` falla
  antes de tocar cualquier pedido.
- Tres funciones en `orders/functions/`, cada una responsable de una etapa,
  compuestas por `import_falabella_orders` (el proceso registrado en
  `orchestrator`):
  1. `fetch_falabella_orders`: trae pedidos de Falabella (últimas 48h por
     defecto, con solape) y los persiste si no existen ya localmente. No
     toca Shopify. Acepta `created_after` (datetime o string ISO-8601) para
     acotar desde cuándo traer — útil para una primera corrida (ej. "desde
     hoy en adelante") en vez del lookback de 48h.
  2. `process_pending_orders`: por cada `MarketplaceOrder` **de
     Falabella** pendiente, llama `process_shipment([order], cliente_fijo)`.
     El `note` de la orden identifica al comprador real:
     `"Falabella #<numero> — <nombre> <apellido> CC <cedula>"`.
  3. `import_falabella_orders`: compone las dos etapas anteriores (ya no
     reconcilia el directorio de `customers`). Acepta `params={"limit": N}` para
     limitar cuántos pedidos pendientes se escriben en Shopify en esa
     corrida — pensado para una primera corrida controlada contra la
     cuenta real (`import_falabella_orders(params={"limit": 1})`), no para
     uso normal. No limita el descubrimiento de pedidos nuevos, solo el
     paso que escribe en Shopify.
- `process_shipment(orders, customer_id)` (`orders/functions/process_shipment.py`):
  **punto único de "crear la orden en Shopify"** para todos los canales.
  Recibe las órdenes de un mismo envío (Falabella: una; Mercado Libre: las
  de un pack), resuelve SKUs (`integrations.shopify.get_variant_inventory_by_sku`,
  en vivo y siempre, aunque el variant id esté cacheado — sin tabla de
  equivalencias todavía), elige **una** bodega para todo el envío y crea
  **una** orden de Shopify con todas las líneas. El resultado (estado,
  `shopify_order_id`, bodega) queda igual en cada orden del envío.
- `claim_orders(marketplace, order_ids)` (`orders/functions/claim_orders.py`):
  **reclamo atómico** compartido por Mercado Libre y Madecentro. Pasa a
  `procesando` todas las órdenes del envío o ninguna, bajo bloqueo de
  fila, y solo si están en `RETRYABLE_STATUSES` (`pending` /
  `error_creando_orden`) sin `shopify_order_id`. Es lo que evita crear
  dos órdenes en Shopify cuando dos procesos llegan a la vez al mismo
  pedido. Un canal nuevo con webhook y recuperación lo reutiliza; no se
  reimplementa.
- **Regla junto al reclamo**: antes de reclamar, los datos de una fila se
  guardan con un `update` condicionado a `shopify_order_id=""` y
  `status__in=RETRYABLE_STATUSES`, **nunca con `row.save()`**. Un `save()`
  completo con una copia leída antes del reclamo de otro proceso devuelve
  la fila a `pending` y permite un segundo reclamo. Así se duplicó en
  Shopify el pedido de Mercado Libre 2000018635989514 (#20131 y #20132) el
  2026-09-25, con dos avisos que llegaron a 100 ms uno del otro. Los ítems
  se crean **después** del reclamo, por el proceso que lo ganó.

## Mercado Libre (webhook)

Plan: [`../implementations-plans/mercadolibre-orders-import.md`](../implementations-plans/mercadolibre-orders-import.md).

- **Disparo**: `POST /api/orders/webhooks/mercadolibre/`
  (`orders/webhooks.py`). Mercado Libre no firma: se valida tópico
  `orders_v2`, app (`MERCADOLIBRE_CLIENT_ID`) y cuenta conectada
  (`parse_mercadolibre_notification`) y se lanza
  `orders.process_mercadolibre_notification` (`allow_concurrent=True`).
- **`process_mercadolibre_order(order_id)`** (lo usan el webhook y la
  recuperación, idempotente): crea primero un marcador local, lee el
  pedido de la API; no pagado o Full → descarta el marcador; si es de un
  pack, junta todas las órdenes (`get_pack`) y solo sigue cuando todas
  están pagadas y los ítems suman lo que lleva el envío; reclama
  atómicamente **todas** las órdenes del envío (`procesando`) y llama
  `process_shipment`. Número visible: `pack_id` si existe, si no el id de
  la orden.
- Datos del comprador desde facturación (`get_billing_info`):
  `customer_identification_type` (`CC`/`NIT`), `customer_identification`,
  `customer_type` (`CO` persona / `BU` empresa), nombre (razón social en
  NIT), dirección, ciudad, departamento. **Nunca email ni teléfono**
  (Mercado Libre no los entrega). `shipment_id` agrupa las órdenes de un
  envío.
- Cliente fijo: `MERCADOLIBRE_SHOPIFY_CUSTOMER_ID`; sin él, el proceso
  falla antes de tocar nada. Nota en Shopify:
  `"Mercado Libre #<pack o id> — <nombre> <CC|NIT> <documento>"`.
- **Recuperación** `orders.recover_mercadolibre` (programada,
  `allow_concurrent=False`): procesa los avisos de `missed_feeds` y los
  pedidos de Mercado Libre en `pending`/`error_creando_orden` sin cambios
  hace más de 15 min (incluye los marcadores de procesos que fallaron).
  Nunca toca `procesando`. Reporta packs incompletos hace más de 6 h. Si
  algún pedido falla, la ejecución termina en error con el resumen.

## Madecentro (rutina por API + webhook en captura)

Plan: [`../implementations-plans/madecentro-orders-import.md`](../implementations-plans/madecentro-orders-import.md).

- **Rutina** `orders.import_madecentro`
  (`orders/functions/import_madecentro_orders.py`, `allow_concurrent=False`,
  se programa por la API del orquestador):
  - Lista los pedidos de los últimos `days` días (`params`, por defecto
    `1`: de ayer a hoy en hora de Colombia) con `list_orders`, recorriendo
    todas las páginas, y se queda con los `paid`. Los `voided` se ignoran.
  - Suma los pedidos de Madecentro guardados en `pending` /
    `error_creando_orden`, aunque estén fuera del rango.
  - `params={"limit": N}` limita cuántos pedidos se procesan en la
    corrida.
  - Un fallo en un pedido no detiene el resto; al final la ejecución
    termina en error con el resumen.
  - Sirve de importación mientras no hay webhook y, después, de
    recuperación.
- **`process_madecentro_order(order_id)`** (idempotente; lo reutilizará el
  webhook):
  - Ya creado o en `procesando` → no se toca y no se llama a la API.
  - Si no, lee el pedido con `get_order`. No pagado o cancelado → se
    descarta la fila pendiente, si existe.
  - Guarda el comprador y los ítems (los ítems una sola vez), reclama la
    fila con `claim_orders` y llama `process_shipment([pedido])`: un pedido
    = un envío.
  - Si falla la API, deja el detalle en `error_description` de la fila
    existente y relanza.
- **Identificadores**: `marketplace_order_id` = id de Shipturtle;
  `marketplace_order_number` = `name` sin `#`.
- **Comprador**: nombre (en empresas, el `name` de facturación), email,
  teléfono, dirección, ciudad y departamento. **Shipturtle no entrega
  documento** (cédula/NIT), así que la nota no lo lleva.
- **Cliente fijo**: `MADECENTRO_SHOPIFY_CUSTOMER_ID`; sin él, falla antes
  de leer nada. Nota en Shopify: `"Madecentro #<name> — <nombre>"`.
- **Webhook temporal**: `POST /api/orders/webhooks/madecentro/`
  (`MadecentroOrderWebhookView`, `orders/webhooks.py`), registrado en
  Shipturtle para order create/update.
- **Qué hace**: solo registra en los logs (nivel `warning`) los headers y
  el body crudo, recortado a 20 KB (`MADECENTRO_CAPTURE_MAX_BYTES`), y
  responde `200`.
- **Qué no hace**: no valida (Shipturtle no ofrece secreto ni firma, a
  confirmar con los avisos reales), no persiste, no lanza procesos y no
  toca Shopify.
- **Riesgo aceptado**: los logs de Railway guardan datos del comprador
  mientras dure la captura. En la fase 1 se reemplaza por el procesamiento
  real y se quita el log del payload completo.

## Bodega de despacho

Plan: [`../implementations-plans/shopify-inventory-by-location.md`](../implementations-plans/shopify-inventory-by-location.md).

- **Una bodega por envío** (un envío = una guía; en Falabella un pedido es
  un envío, en Mercado Libre un pack puede juntar varias órdenes), elegida
  por `orders/functions/select_fulfillment_location.py` (función pura): la
  bodega prioritaria (`FULFILLMENT_PRIORITY_LOCATION_ID`, "Bodega Envia")
  si tiene stock para todos los ítems; si no, la que cubra el pedido
  completo con más unidades (empate → nombre). Ninguna lo cubre, o un ítem
  no controla inventario → **novedad**. El mismo SKU en varias líneas se
  suma antes de evaluar (necesario para envíos con varias órdenes, como
  los packs de Mercado Libre: un envío = una guía = una bodega).
- **La orden en Shopify se crea igual con novedad**: `fulfillment_status` es
  independiente de `status`.
- Campos en `MarketplaceOrder`: `fulfillment_status` (vacío = sin evaluar,
  `asignada`, `novedad`, `resuelta_manual`), `fulfillment_location_id`
  (id de la `Location` de Shopify), `fulfillment_location_name`,
  `fulfillment_note` (motivo de la novedad). En `MarketplaceOrderItem`:
  `inventory_snapshot` (stock por bodega evaluado).
- **Resolver una novedad**: en `/admin/`, filtrar por `fulfillment_status`,
  revisar el `inventory_snapshot` de los ítems, llenar bodega, pasar a
  `resuelta_manual` y dejar nota. El proceso no vuelve a tocar un pedido
  resuelto manualmente.
- Solo se identifica: no se reasigna la bodega dentro de Shopify.

## Estados de un `MarketplaceOrder`

| Estado | Significado |
| --- | --- |
| `pending` | Recién descubierto, no procesado todavía. En Mercado Libre también el marcador que deja el proceso antes de llamar a la API (si `error_description` tiene detalle, el proceso falló y la recuperación lo reintenta) y el pack que espera a sus demás órdenes (`error_description` empieza por "Envío incompleto"). |
| `procesando` | Mercado Libre y Madecentro (`claim_orders`): reclamado por un proceso que está creando la orden en Shopify. Si se queda así, el proceso murió a mitad y no se sabe si la orden quedó creada: **revisar a mano** en Shopify y corregir la fila; nada lo reintenta solo. |
| `error_creando_cliente` | **Obsoleto** — ningún código lo asigna desde el cambio a cliente fijo. Se conserva por filas históricas; como no tienen `shopify_order_id`, la siguiente corrida las reintenta. |
| `error_creando_orden` | Falló la creación de la orden (SKU sin resolver, o Shopify rechazó la orden). `error_description` tiene el detalle. |
| `orden_creada` | Éxito — `shopify_order_id`/`shopify_order_name` quedan guardados junto con `marketplace_order_number`. |

## Reglas de cambio

- No se hace el intento de crear la orden si algún ítem no resuelve un
  `shopify_variant_id` — se marca error completo, no una orden parcial.
- `financial_status="PAID"` es fijo para pedidos de marketplace: llegan ya
  pagados por el comprador, Shopify solo registra la venta.
- El número de orden del marketplace va en el `note` de la orden de
  Shopify (`"{marketplace} #{numero}"`) — no se usa un campo dedicado (no
  se verificó si existe uno).
- **Riesgo conocido, sin resolver todavía para Falabella**: si
  `create_order()` falla por red (no por rechazo de Shopify), no se sabe si
  la orden quedó creada — `orderCreate` no tiene clave de idempotencia
  propia. Con el criterio de "reintenta todo lo que no tenga
  `shopify_order_id`", ese pedido se reintentaría en la próxima corrida sin
  distinción, con riesgo de duplicarlo en Shopify. Ver el plan para el
  detalle — es una decisión de negocio pendiente, no un olvido. **En
  Mercado Libre no aplica**: el pedido queda en `procesando` y no se
  reintenta solo.

## Pruebas

`orders/tests.py`: cada etapa probada por separado con las dependencias
externas mockeadas (nunca red real) — descubrimiento de pedidos nuevos sin
duplicar (con los datos de facturación del comprador), orden creada con el
cliente fijo y el `note` del comprador, fallo temprano sin cliente fijo
configurado, reintento de filas en `error_creando_cliente`, bodega
asignada/novedad (con la orden creada igual), snapshot de inventario,
consulta de inventario con variant id cacheado, no pisar
`resuelta_manual`, la regla de `select_fulfillment_location` caso por
caso (incluida la suma del mismo SKU en varias líneas), error de orden
por SKU o por rechazo de Shopify, que un pedido ya resuelto no se
reprocesa, y que el proceso queda registrado en `orchestrator` (tanto el
`ProcessType` sembrado por migración como el callable en el registro en
memoria).

Mercado Libre (mocks de `integrations.mercadolibre` y Shopify): pedido
pagado creado con el cliente fijo y la nota (CC y NIT con razón social),
no pagado y Full sin rastro, notificación repetida sin efecto, pedido en
`procesando` intacto, pack en una sola orden y una sola bodega, pack sin
bodega que cubra todo → novedad en todas, pack con orden sin pagar o
ítems que no suman el envío → espera, pack reclamado por otro proceso,
otra notificación que reclama la fila mientras esta consulta la
facturación → no se crea dos veces (regresión del duplicado del
2026-09-25; igual en Madecentro),
fallo de Mercado Libre que deja marcador, reintento por SKU sin volver a
pedir facturación, fallo temprano sin cliente fijo, y que el lote de
Falabella ignora pedidos de Mercado Libre. Recuperación: missed feeds y
pedidos viejos, exclusiones (recientes, `procesando`, creados, Falabella),
un fallo no detiene el resto pero marca error, reporte de packs
incompletos. Webhook: válido lanza el proceso; app, cuenta, tópico o
recurso inválidos y sin cuenta conectada responden `200` sin lanzar.

Madecentro (mocks de `integrations.madecentro` y Shopify):
- `process_madecentro_order`:
  - pedido pagado creado con el cliente fijo, la nota y la etiqueta;
  - un pedido ya creado o en `procesando` no se relee;
  - un pedido `voided` o cancelado descarta la fila pendiente;
  - un error de SKU queda marcado y el reintento crea la orden sin
    duplicar ítems;
  - un fallo de la API queda en la fila y se relanza;
  - falla temprano sin cliente fijo.
- Rutina:
  - solo procesa pagados, con rango por defecto de un día;
  - `days` amplía el rango y se leen todas las páginas;
  - se reintentan los guardados pendientes o en error sin duplicar, y se
    excluyen `procesando`, creados y otros canales;
  - `limit` tope de pedidos;
  - un fallo no detiene el resto pero marca error;
  - el `ProcessType` queda sembrado.

Madecentro (captura): una petición anónima con JSON responde `200` y queda
en el log (headers y body), un cuerpo que no es JSON se acepta, un body
grande queda recortado, y nunca se lanza un proceso.

Esa última prueba (`test_process_registered_in_orchestrator_registry`)
falla hoy: `orchestrator/registrations.py` solo se carga con
`RUN_MAIN=true` o `ORCHESTRATOR_FORCE_READY` (ver
[`orchestrator.md`](orchestrator.md)), y la corrida de pruebas no define
ninguno de los dos.
