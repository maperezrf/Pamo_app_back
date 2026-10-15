# Plan: ver desde qué bodegas sale cada pedido (fulfillment orders de Shopify)

Estado: **implementado en código (2026-10-15), sin desplegar**. Rol:
arquitecto ([`../roles/ARCHITECT.md`](../roles/ARCHITECT.md)). Lo vigente
ya está en `docs/apps/orders.md`, `docs/apps/integrations.md` y
`docs/contracts/API.md`. Pendiente para borrar este plan:

1. Desplegar backend (migración `0020_shopify_fulfillment_orders`) y
   frontend juntos.
2. Correr `orders.backfill_shopify_orders` con `created_from` para llenar
   los pedidos existentes.
3. Verificar con un pedido real si mover o cumplir un fulfillment order
   dispara `orders/updated` (ver "Riesgos").

## Objetivo

En la tabla de pedidos, ver **desde qué bodega(s) sale cada pedido** y qué
productos salen de cada una, tal como lo tiene Shopify (sus *fulfillment
orders*), con la guía si esa bodega ya fue cumplida.

Es **solo lectura**: no escribe en Shopify, no cambia `Dispatch`, el
proceso de despacho ni los botones. El plan grande
([`split-dispatch-by-warehouse.md`](split-dispatch-by-warehouse.md)) sigue
aparte; este cambio no toma ninguna de sus decisiones.

Por qué Shopify: la fase 0 de ese plan mostró que Shopify ya divide pedidos
web (14 de 131 en varias bodegas) y que la app de Envía y las personas
cumplen cada fulfillment order con su guía. Este backend hoy solo guarda
`displayFulfillmentStatus`.

## Estado actual verificado en código

- `ORDER_FIELDS` (`integrations/shopify/queries.py:177`) lo comparten
  `LIST_ORDERS_PAGE` (50 pedidos por página, costo medido 72 puntos) y
  `GET_ORDER`. No pide fulfillment orders ni fulfillments.
- `normalize_order` (`integrations/shopify/functions/normalize_order.py`)
  es la única normalización.
- `upsert_shopify_order` (`orders/functions/upsert_shopify_order.py`) es el
  único guardado. Solo descarta datos **más viejos** (`<`), así que con el
  mismo `updated_at` vuelve a escribir: un backfill rellena lo existente.
- `format_orders` (`orders/functions/list_orders.py:61`) arma cada pedido
  del listado.

## Diseño

### 1. Consulta (`integrations/shopify/queries.py`)

Fragmento nuevo `FulfillmentFields`, incluido en `LIST_ORDERS_PAGE` y
`GET_ORDER`:

```graphql
fulfillmentOrders(first: 10) {
  nodes {
    id
    status
    assignedLocation { name location { id } }
    lineItems(first: 50) { nodes { totalQuantity lineItem { sku name } } }
    fulfillments(first: 5) { nodes { trackingInfo(first: 1) { number company url } } }
  }
}
```

**Verificar antes de escribirlo** (introspección contra la tienda, API
`2024-07`): los nombres exactos de los campos y el `requestedQueryCost`
de `LIST_ORDERS_PAGE`. Los conteos anidados multiplican el costo por los 50
pedidos de la página. Si se pasa de 1000, bajar `first` de los anidados o
el tamaño de página (`iter_shopify_order_pages`), y dejar el costo medido
en el comentario de la consulta, como el actual.

### 2. Normalización (`normalize_order`)

Clave nueva `fulfillment_orders`, una por fulfillment order **sin los
`CANCELLED`** (si una prueba real muestra que un movimiento de bodega deja
otros estados de sobra, se ajusta):

```python
{"id": "…", "location_id": "…", "location_name": "Bodega Envia",
 "status": "OPEN", "items": [{"sku": "…", "name": "…", "quantity": 2}],
 "tracking": {"number": "…", "company": "…", "url": "…"}}
```

`quantity` es `totalQuantity`. `tracking` es la guía del último fulfillment
de **ese** fulfillment order que tenga número (o vacíos si no hay). Ids sin
`gid://`.

### 3. Copia local (`orders`): modelo

La página **nunca** consulta Shopify: lee estas tablas, que se mantienen
con el mismo camino que ya tienen los pedidos (webhook `orders/updated` +
reconciliación cada 30 min, todo por `upsert_shopify_order`).

- **`ShopifyFulfillmentOrder`** (nuevo): `order` FK a `ShopifyOrder`
  (`related_name="fulfillment_orders"`, `CASCADE`), `shopify_id` (id del
  fulfillment order), `status`, `location_id`, `location_name`,
  `tracking_number`, `tracking_company`, `tracking_url`, `position`.
  El `shopify_id` es lo que usará más adelante la subida de la guía a
  Shopify.
- **`ShopifyFulfillmentOrderLine`** (nuevo): `fulfillment_order` FK
  (`related_name="lines"`), `sku`, `name`, `quantity`, `position`.
- `upsert_shopify_order._upsert`: dentro de la misma transacción, igual
  que las líneas del pedido, borra los fulfillment orders del pedido y los
  vuelve a crear con `bulk_create` (los de las líneas, después de tener los
  ids de los padres). Es una foto de Shopify: no se edita aquí.
- Admin: inline de solo lectura en `ShopifyOrder`, si ya tiene admin.

### 4. Listado (`GET /api/orders/`)

`filter_orders` agrega `"fulfillment_orders__lines"` a su
`prefetch_related` (`list_orders.py:37`); `_format_order` agrega
`"fulfillment_orders"` con la forma de la sección 2. Son dos consultas más
por página, no una por pedido.

### 5. Frontend (`frontend/src/modules/orders/`)

- `OrderExpandedDetail.jsx`: recuadro nuevo "Bodegas (Shopify)" arriba de
  la tabla de productos, con una fila por bodega: nombre, estado
  (`orderFormat.js`: Abierto, En proceso, Cumplido…), productos con
  cantidad y guía (número, transportadora y enlace si hay `url`). Sin
  fulfillment orders → "Sin datos de Shopify todavía".
- `OrdersPage.jsx`: en la columna "Bodega", si hay más de una, un chip
  "N bodegas" con los nombres en el `title`. Con una, igual que hoy.
- El recuadro de despacho (`Dispatch`) y sus botones **no cambian**.

## Carga de lo existente

Después de desplegar: `orders.backfill_shopify_orders` con
`created_from` (p. ej. 30 días) por la API del orquestador. Rellena
`fulfillment_orders` en los pedidos que ya están.

## Riesgos

- **Si mover un fulfillment order o cumplirlo no dispara `orders/updated`
  ni cambia el `updatedAt` del pedido**, la copia queda vieja hasta el
  próximo cambio del pedido: ni el webhook ni la reconciliación (que filtra
  por `updated_at`) lo traerían. Verificar con un pedido real: mirar
  `updatedAt` antes y después de que la app de Envía lo cumpla, y si
  llegó el webhook. Cumplir cambia `displayFulfillmentStatus`, así que lo
  esperado es que sí avise; mover de bodega es lo dudoso. Si no avisa, el
  paso siguiente (aparte) es suscribir en el admin de Shopify los eventos de
  fulfillment al mismo webhook, que ya relee el pedido completo.
- Costo de la consulta del listado (ver 1).

## Pruebas (sin red)

- `normalize_order`: pedido con dos fulfillment orders en bodegas
  distintas; uno `CANCELLED` excluido; guía tomada de su propio
  fulfillment; sin fulfillments → `tracking` vacío.
- `upsert_shopify_order`: crea los fulfillment orders con sus líneas; un
  segundo guardado los reemplaza (no duplica); un dato viejo (`SKIPPED`)
  no los toca.
- Listado: `fulfillment_orders` en la respuesta y número de consultas
  fijo por página (`assertNumQueries`). Las pruebas de permisos de
  `OrderListAPI` ya existen y no cambian.
- `python manage.py check` y `python manage.py test orders integrations`.

## Documentos a actualizar

`docs/apps/orders.md` ("Copia local de pedidos de Shopify" y "Listado"),
`docs/contracts/API.md` (`fulfillment_orders` en `GET /api/orders/`),
`docs/apps/integrations.md` (forma de `normalize_order`); en el frontend,
`docs/apps/orders.md` y `docs/contracts/API_CONSUMPTION.md`. Al terminar,
este plan se borra.
