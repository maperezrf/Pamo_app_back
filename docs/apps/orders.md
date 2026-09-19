# App `orders`

`orders/` orquesta la importación de pedidos de marketplaces (Falabella
hoy, otros a futuro) hacia Shopify. No es transporte de proveedor (eso vive
en `integrations/`) ni dueña del directorio de clientes (eso es
`customers/`) — solo decide *cuándo* y *cómo* combinar ambos para crear
pedidos reales en Shopify. Plan completo en
[`../implementations-plans/marketplace-orders-import.md`](../implementations-plans/marketplace-orders-import.md).

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
- Tres funciones en `orders/functions/`, cada una responsable de una etapa,
  compuestas por `import_falabella_orders` (el proceso registrado en
  `orchestrator`):
  1. `fetch_falabella_orders`: trae pedidos de Falabella (últimas 48h por
     defecto, con solape) y los persiste si no existen ya localmente. No
     toca Shopify. Acepta `created_after` (datetime o string ISO-8601) para
     acotar desde cuándo traer — útil para una primera corrida (ej. "desde
     hoy en adelante") en vez del lookback de 48h.
  2. `process_pending_orders`: por cada `MarketplaceOrder` pendiente,
     resuelve cliente (`customers.find_by_identification`, o lo crea si
     hace falta) y SKUs (`integrations.shopify.get_variant_by_sku`, en
     vivo — sin tabla de equivalencias todavía, ver plan), y crea la orden
     en Shopify.
  3. `import_falabella_orders`: compone las tres etapas, empezando por
     `customers.reconcile_from_shopify` para refrescar el directorio de
     clientes antes de resolver nada. Acepta `params={"limit": N}` para
     limitar cuántos pedidos pendientes se escriben en Shopify en esa
     corrida — pensado para una primera corrida controlada contra la
     cuenta real (`import_falabella_orders(params={"limit": 1})`), no para
     uso normal. No limita el descubrimiento de pedidos nuevos, solo el
     paso que escribe en Shopify.

## Estados de un `MarketplaceOrder`

| Estado | Significado |
| --- | --- |
| `pending` | Recién descubierto, no procesado todavía. |
| `error_creando_cliente` | No se pudo resolver/crear el cliente en Shopify (ej. sin email ni teléfono — caso real confirmado en pedidos de Falabella con envío Dropshipping). `error_description` tiene el detalle. |
| `error_creando_orden` | Cliente resuelto, pero falló algo después (SKU sin resolver, o Shopify rechazó la orden). `error_description` tiene el detalle. |
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
duplicar, los tres estados finales de un pedido (éxito, error de cliente,
error de orden por SKU o por rechazo de Shopify), que un pedido ya resuelto
no se reprocesa, y que el proceso queda registrado en `orchestrator` (tanto
el `ProcessType` sembrado por migración como el callable en el registro en
memoria).
