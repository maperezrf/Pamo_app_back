# App `orders`

`orders/` orquesta la importación de pedidos de marketplaces (Falabella
hoy, otros a futuro) hacia Shopify. No es transporte de proveedor (eso vive
en `integrations/`) y no depende de `customers/`: todos los pedidos de
Falabella se crean en Shopify a nombre de un **cliente fijo**, y los datos
reales del comprador se guardan aquí para facturar después en Siigo. Planes
en
[`../implementations-plans/marketplace-orders-import.md`](../implementations-plans/marketplace-orders-import.md)
y
[`../implementations-plans/falabella-fixed-customer.md`](../implementations-plans/falabella-fixed-customer.md)
(cliente fijo, vigente).

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
  2. `process_pending_orders`: por cada `MarketplaceOrder` pendiente,
     resuelve SKUs (`integrations.shopify.get_variant_inventory_by_sku`, en
     vivo y siempre, aunque el variant id esté cacheado — sin tabla de
     equivalencias todavía, ver plan), elige la bodega de despacho y crea
     la orden en Shopify a nombre del cliente fijo. El `note` de la orden
     identifica al comprador real:
     `"Falabella #<numero> — <nombre> <apellido> CC <cedula>"`.
  3. `import_falabella_orders`: compone las dos etapas anteriores (ya no
     reconcilia el directorio de `customers`). Acepta `params={"limit": N}` para
     limitar cuántos pedidos pendientes se escriben en Shopify en esa
     corrida — pensado para una primera corrida controlada contra la
     cuenta real (`import_falabella_orders(params={"limit": 1})`), no para
     uso normal. No limita el descubrimiento de pedidos nuevos, solo el
     paso que escribe en Shopify.

## Bodega de despacho

Plan: [`../implementations-plans/shopify-inventory-by-location.md`](../implementations-plans/shopify-inventory-by-location.md).

- **Una bodega por pedido** (el marketplace da una guía por pedido), elegida
  por `orders/functions/select_fulfillment_location.py` (función pura): la
  bodega prioritaria (`FULFILLMENT_PRIORITY_LOCATION_ID`, "Bodega Envia")
  si tiene stock para todos los ítems; si no, la que cubra el pedido
  completo con más unidades (empate → nombre). Ninguna lo cubre, o un ítem
  no controla inventario → **novedad**.
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
| `pending` | Recién descubierto, no procesado todavía. |
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
- **Riesgo conocido, sin resolver todavía**: si `create_order()` falla por
  red (no por rechazo de Shopify), no se sabe si la orden quedó creada —
  `orderCreate` no tiene clave de idempotencia propia. Con el criterio de
  "reintenta todo lo que no tenga `shopify_order_id`", ese pedido se
  reintentaría en la próxima corrida sin distinción, con riesgo de
  duplicarlo en Shopify. Ver el plan para el detalle — es una decisión de
  negocio pendiente, no un olvido.

## Pruebas

`orders/tests.py`: cada etapa probada por separado con las dependencias
externas mockeadas (nunca red real) — descubrimiento de pedidos nuevos sin
duplicar (con los datos de facturación del comprador), orden creada con el
cliente fijo y el `note` del comprador, fallo temprano sin cliente fijo
configurado, reintento de filas en `error_creando_cliente`, bodega
asignada/novedad (con la orden creada igual), snapshot de inventario,
consulta de inventario con variant id cacheado, no pisar
`resuelta_manual`, la regla de `select_fulfillment_location` caso por
caso, error de orden
por SKU o por rechazo de Shopify, que un pedido ya resuelto no se
reprocesa, y que el proceso queda registrado en `orchestrator` (tanto el
`ProcessType` sembrado por migración como el callable en el registro en
memoria).

Esa última prueba (`test_process_registered_in_orchestrator_registry`)
falla hoy: `orchestrator/registrations.py` solo se carga con
`RUN_MAIN=true` o `ORCHESTRATOR_FORCE_READY` (ver
[`orchestrator.md`](orchestrator.md)), y la corrida de pruebas no define
ninguno de los dos.
