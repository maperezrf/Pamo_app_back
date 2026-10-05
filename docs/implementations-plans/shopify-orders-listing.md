# Plan: listado de pedidos para el frontend desde Shopify

Estado: **implementado (2026-10-05); fuente de datos reemplazada** por
[`shopify-orders-local-sync.md`](shopify-orders-local-sync.md): el listado
ya no consulta Shopify en vivo sino la copia local, y pagina por número de
página. La forma de cada pedido descrita aquí sigue vigente. Las decisiones las confirmó el
usuario el 2026-10-05, salvo las marcadas como "supuesto" en "Decisiones".
Lo que cambió respecto al diseño, y lo verificado contra la tienda real, está
en "Notas de implementación".

## Contexto

Todos los pedidos terminan en Shopify: los de marketplace los crea
`orders.process_shipment` y los de la tienda web nacen allí. El frontend
necesita un listado de ventas con el pedido, el cliente, los productos, el
marketplace de origen y los totales.

Shopify no basta por sí solo. Los pedidos de marketplace se crean a nombre
de un **cliente fijo por canal** (ver [`../apps/orders.md`](../apps/orders.md)),
y el comprador real solo está en `MarketplaceOrder`. Por eso el listado lee
Shopify y completa el cliente con la tabla local.

## Objetivo

Un solo endpoint, `GET /api/orders/`, con filtros y paginación. Cada pedido
trae:

- pedido: id y nombre de Shopify, fecha, estado financiero y de despacho;
- marketplace de origen y número del pedido en ese marketplace;
- cliente: el comprador real;
- productos: SKU, nombre, cantidad, precio unitario y total de la línea
  (ej. 2 × 100 = 200);
- total de la venta.

En la misma respuesta, aparte, van los pedidos de marketplace que **no se
pudieron crear en Shopify** (ej. SKU no encontrado), con el error guardado en
la base de datos, para alertar al equipo. Ver "Pedidos no creados".

Fuera de alcance: endpoint de detalle, costo unitario o margen, impuestos y
envío desglosados, escritura en Shopify.

## Decisiones

| Tema | Decisión |
| --- | --- |
| Fuente | Shopify en vivo, en cada petición. No se crea una copia local de pedidos. |
| Cliente | Si el pedido de Shopify tiene `MarketplaceOrder` (unido por `shopify_order_id`), se usa el comprador de esa fila. Si no, el cliente de Shopify, con la cédula de `defaultAddress.company` (convención del proyecto, ver [`../apps/customers.md`](../apps/customers.md)). |
| Marketplace | Por la etiqueta que pone `process_shipment` (`tags=[order.marketplace]`): `falabella`, `mercadolibre`, `madecentro`, `sodimac`. Sin ninguna de ellas → `shopify` (tienda web o venta directa). |
| Totales | Solo `total` del pedido y, por línea, `unit_price` y `line_total` (`unit_price × quantity`). Sin desglose de impuestos, envío ni descuentos. |
| Paginación | Por cursor, porque Shopify no da offset ni conteo total. Respuesta `next_cursor` / `has_next_page`; sin `count`. Solo hacia adelante: el frontend guarda los cursores de las páginas ya vistas para volver. |
| Orden | Más reciente primero (`sortKey: CREATED_AT, reverse: true`). |
| Roles | **Supuesto:** `ORDERS_LIST_ROLES = ["Admin", "Operaciones"]`, los mismos de `orchestrator/apis.py`. Cambiar ahí si el negocio define otro rol. |
| Tamaño de página | **Supuesto:** 20 por defecto, máximo 50, condicionado al costo de la consulta (ver "Riesgos"). |
| Pedidos no creados | Van en la misma respuesta, en una lista aparte (`orders_not_created`), leídos solo de `MarketplaceOrder` (no existen en Shopify). El error se devuelve tal cual está en `error_description`. |
| Qué cuenta como "no creado" | **Supuesto:** `shopify_order_id` vacío y estado `error_creando_orden`, `error_creando_cliente` (histórico), `procesando` (atascado, requiere revisión manual) o `pending` con `error_description` no vacío (fallo de API o pack de Mercado Libre incompleto). Un `pending` sin error es un pedido recién llegado y no se alerta. |

## Contrato propuesto

`GET /api/orders/`

Query (todos opcionales):

| Parámetro | Valor | Traducción |
| --- | --- | --- |
| `marketplace` | `falabella`, `mercadolibre`, `madecentro`, `sodimac`, `shopify` | `tag:<valor>`; `shopify` → `-tag:falabella -tag:mercadolibre ...` (todas las de `Marketplace`). |
| `date_from`, `date_to` | `YYYY-MM-DD`, inclusive, hora de Colombia | `created_at:>=` / `created_at:<` del día siguiente. |
| `search` | número de Shopify (`#20131` o `20131`) o número del marketplace | Ver "Búsqueda". |
| `cursor` | `next_cursor` de la página anterior | `after`. |
| `page_size` | 1–50 | `first`. |

Respuesta `200`:

```json
{
  "orders": [
    {
      "shopify_order_id": "1234567890",
      "shopify_order_name": "20131",
      "created_at": "2026-10-04T15:20:00-05:00",
      "financial_status": "PAID",
      "fulfillment_status": "UNFULFILLED",
      "marketplace": "mercadolibre",
      "marketplace_order_numbers": ["2000018635989514"],
      "customer": {
        "identification_type": "CC",
        "identification": "1020304050",
        "first_name": "Ana",
        "last_name": "Pérez",
        "email": "",
        "phone": "",
        "city": "Bogotá",
        "region": "Cundinamarca",
        "address": "Cra 1 # 2-3"
      },
      "fulfillment": {
        "status": "asignada",
        "location_id": "71234567",
        "location_name": "Bodega Envia",
        "note": ""
      },
      "items": [
        {"sku": "PAC8424", "name": "…", "quantity": 2, "unit_price": "100.00", "line_total": "200.00"}
      ],
      "line_items_truncated": false,
      "total": "200.00",
      "currency": "COP"
    }
  ],
  "next_cursor": "eyJsYXN0X2lk…",
  "has_next_page": true,
  "orders_not_created": [
    {
      "id": 812,
      "marketplace": "madecentro",
      "marketplace_order_number": "MC1045",
      "shipment_id": "",
      "created_at": "2026-10-04T10:02:00-05:00",
      "updated_at": "2026-10-04T10:05:00-05:00",
      "status": "error_creando_orden",
      "error": "SKU no encontrado en Shopify: PMO-028-MP (equivalencia PAC8424)",
      "customer": { "...": "misma forma que en orders" },
      "fulfillment": { "...": "misma forma que en orders; normalmente vacía" },
      "items": [
        {"sku": "PMO-028-MP", "quantity": 2, "unit_price": "100.00", "line_total": "200.00"}
      ],
      "total": "200.00"
    }
  ],
  "orders_not_created_count": 1
}
```

- `orders_not_created[].items[].sku` es el SKU **del marketplace**
  (`MarketplaceOrderItem.marketplace_sku`): es el que no se pudo resolver.
- `total` es la suma de `line_total` de los ítems locales.

- `marketplace_order_numbers` es lista porque un pack de Mercado Libre junta
  varias `MarketplaceOrder` en una sola orden de Shopify. Vacía en pedidos
  `shopify`.
- Importes como string con dos decimales, como en el resto del proyecto
  (`Decimal`), nunca `float`.
- Un campo que no existe en la fuente va como `""`, no se omite.

Errores:

- `400` si un parámetro es inválido (marketplace desconocido, fecha mal
  formada, `date_from > date_to`, `page_size` fuera de rango), con el
  detalle por campo del serializer.
- `403` sin sesión o sin rol.
- `502` `{"detail": "No se pudo consultar Shopify."}` si Shopify falla. Sin
  el mensaje crudo del proveedor.

## Pedidos no creados

- Fuente: `MarketplaceOrder` con `prefetch_related("items")`, filtrada con
  la regla de "Decisiones". Shopify no se consulta para esta lista.
- Mismos filtros que `orders`:
  - `marketplace`: por el campo `marketplace`; `shopify` → lista vacía.
  - `date_from` / `date_to`: sobre `created_at` de la fila (cuándo llegó al
    sistema), en hora de Colombia.
  - `search`: `marketplace_order_number` exacto.
- No usa el cursor de Shopify. Se devuelve **completa en cada página**
  (una consulta local), más reciente primero, con un tope de 200 filas y
  `orders_not_created_count` con el total real. El frontend puede mostrarla
  solo con la primera página.
- Un pack de Mercado Libre que falla deja el mismo error en todas sus
  filas: se devuelve una entrada por fila con su `shipment_id`, para que el
  frontend las agrupe si quiere.
- El texto de `error` lo escriben hoy `process_shipment` (`SKU no
  encontrado en Shopify: …`, `SKU X: <kit vacío>`, rechazo de Shopify) y
  los procesos de cada canal. No se crea un código de error en esta
  versión; si el frontend necesita clasificarlos, la mejora es un campo
  `error_code` asignado en `_mark_error`, no interpretar el texto.

## Búsqueda

1. Se busca primero en local: `MarketplaceOrder` con
   `marketplace_order_number = search` y `shopify_order_id` no vacío. Si
   hay coincidencias, la consulta a Shopify es `id:<a> OR id:<b>`.
2. Si no, se envía a Shopify como `name:#<search>` (con `#` antepuesto si
   no viene).
3. `search` se combina con los demás filtros por `AND`.

Los valores que van a la cadena `query` de Shopify se escapan igual que en
`get_variants_by_skus` (comillas y barras).

## Archivos

### Integración (transporte y normalización)

- `integrations/shopify/queries.py`: nueva consulta `LIST_ORDERS_PAGE`.
  Campos mínimos:
  - `orders(first, after, query, sortKey: CREATED_AT, reverse: true)` con
    `pageInfo { hasNextPage endCursor }`;
  - por orden: `id name createdAt displayFinancialStatus
    displayFulfillmentStatus tags totalPriceSet { shopMoney { amount
    currencyCode } }`;
  - `customer { id firstName lastName email phone defaultAddress {
    company city province address1 } }`;
  - `lineItems(first: 30) { pageInfo { hasNextPage } nodes { sku name
    quantity originalUnitPriceSet { shopMoney { amount } } } }`.

  Verificar los nombres de campo **por introspección** contra la versión
  de API que usa la tienda, como se hizo con `CREATE_ORDER`. No adivinar.
- `integrations/shopify/functions/list_orders_page.py`:
  `list_orders_page(*, first, after=None, query="") →
  {"orders": [...], "has_next_page", "end_cursor"}`. Sigue la forma de
  `list_customers_page.py`: normaliza ids sin prefijo `gid://`, vacíos a
  `""`, y no conoce marketplaces ni `MarketplaceOrder`. Si una orden trae
  más líneas que el tope, marca `line_items_truncated: true`.
- Exportarla en `integrations/shopify/functions/__init__.py` si las demás
  lo están.

### Negocio (`orders`)

- `orders/functions/list_shopify_orders.py`:
  `list_shopify_orders(*, marketplace=None, date_from=None, date_to=None,
  search="", cursor=None, page_size=20) → {"orders", "next_cursor",
  "has_next_page"}`.
  1. Arma la cadena `query` desde los filtros (función auxiliar pura,
     probada aparte).
  2. Llama `list_orders_page`.
  3. Una sola consulta local:
     `MarketplaceOrder.objects.filter(shopify_order_id__in=ids)`, agrupada
     por `shopify_order_id`. Nunca una consulta por pedido.
  4. Arma cada resultado: marketplace por etiqueta (lista de
     `products.models.Marketplace`), números del marketplace y comprador
     desde la fila local (primera del grupo); si no hay fila, cliente de
     Shopify. `line_total = unit_price × quantity` con `Decimal`.
  - Si la etiqueta dice un marketplace pero no hay fila local (pedido
    anterior a este sistema, creado por `pamo_web`), se devuelve el
    marketplace de la etiqueta y el cliente de Shopify.
- `orders/functions/list_orders_not_created.py`:
  `list_orders_not_created(*, marketplace=None, date_from=None,
  date_to=None, search="", limit=200) → {"orders_not_created",
  "orders_not_created_count"}`. Solo base de datos. La regla de qué cuenta
  como "no creado" vive aquí, en un `Q` con nombre, junto a
  `RETRYABLE_STATUSES` si conviene reutilizarlo.
- La vista llama las dos funciones y une los dos dicts. Si Shopify falla,
  responde `502` completo (no devuelve solo la lista local) — **supuesto**;
  la alternativa es devolver `orders_not_created` con `orders: null` y un
  aviso, si el equipo prefiere ver las alertas aunque Shopify esté caído.
- `orders/serializers.py` (crear si no existe):
  `OrderListQuerySerializer` valida los parámetros de entrada. La salida
  la arma la función; no hace falta serializer de salida mientras sea un
  dict plano.
- `orders/apis.py`: `OrderListAPI(RoleRequiredMixin, APIView)` con
  `allowed_roles = ORDERS_LIST_ROLES`. Valida con el serializer, llama la
  función y traduce el error de Shopify a `502`. Sin lógica de negocio.
  **No tocar `GetOrdersTest`**: se conserva a propósito.
- `orders/urls.py`: `path("", OrderListAPI.as_view(), name="orders-list")`.
- Migración opcional: `db_index=True` en `MarketplaceOrder.shopify_order_id`.
  Hoy no tiene índice y el listado filtra por ese campo en cada petición.

## Reutilización

- `ShopifyClient.request_graphql` y su manejo de errores
  (`ShopifyGraphQLError`): no se crea otro cliente.
- Forma de paginación y normalización de `list_customers_page`.
- Escape de valores en la `query` de Shopify de `get_variants_by_skus`.
- `RoleRequiredMixin` de `accounts/permissions.py`.
- `products.models.Marketplace` como única lista de canales: un marketplace
  nuevo aparece solo en el filtro y en la detección por etiqueta.

## Riesgos y vacíos a verificar

1. **Etiquetas históricas**: solo se sabe que `process_shipment` etiqueta
   con el valor de `Marketplace`. Los pedidos creados antes por `pamo_web`
   u otros flujos pueden tener otras etiquetas o ninguna, y saldrían como
   `shopify`. Verificar en la tienda real (lectura) qué etiquetas tienen
   pedidos viejos de cada canal antes de cerrar la regla.
2. **Pedidos de más de 60 días**: Shopify solo los devuelve con el scope
   `read_all_orders`. Confirmar los scopes del token
   (`SHOPIFY_ACCESS_TOKEN`); si no lo tiene, el filtro por fechas antiguas
   devolverá vacío sin error.
3. **Costo de la consulta**: el límite por consulta es 1000 puntos.
   `first × (1 + líneas)` se acerca rápido (50 × 31 ≈ 1550). Hacer una
   llamada manual de solo lectura, revisar `extensions.cost` y fijar el
   máximo de `page_size` y de `lineItems` con ese dato.
4. **Latencia**: cada página es una llamada en vivo a Shopify (timeout de
   30 s del cliente). Aceptable para una primera versión; si el frontend
   lo sufre, la alternativa es un caché corto, no una copia local.
5. **Reembolsos**: `quantity` es la cantidad original; no refleja
   devoluciones. Si importa, usar `currentQuantity` (verificar por
   introspección).
6. **Búsqueda por `name`**: confirmar en la tienda real que `name:#20131`
   devuelve coincidencia exacta.

## Pruebas

Sin red real; Shopify simulado como en el resto de `integrations/` y
`orders/`.

- `integrations/shopify/tests.py`: normalización de una página (ids sin
  prefijo, vacíos, cliente nulo, `line_items_truncated`), y que un error de
  GraphQL se propaga.
- `orders/tests.py`:
  - construcción de la `query`: cada filtro solo y combinados, `shopify`
    excluyendo todas las etiquetas, fechas en hora de Colombia, escape;
  - pedido de marketplace con fila local → comprador real y número;
  - pack de Mercado Libre (varias filas, una orden) → un resultado con
    varios números;
  - pedido web → cliente de Shopify con cédula de `company`, marketplace
    `shopify`;
  - etiqueta de marketplace sin fila local;
  - `line_total` y `total` con `Decimal`;
  - búsqueda resuelta en local vs enviada a Shopify;
  - una sola consulta a `MarketplaceOrder` por página
    (`assertNumQueries`).
  - pedidos no creados: entra cada estado de la regla (incluido `pending`
    con error) y quedan fuera `pending` sin error, `orden_creada` y filas
    con `shopify_order_id`; el `error` sale tal cual; filtros por
    marketplace, fechas y búsqueda; `shopify` → vacía; tope de 200 con el
    conteo real; ítems con `line_total` y `total`; sin consultas N+1.
- API: `200` con rol, `403` sin sesión y sin rol, `400` por cada parámetro
  inválido, `502` cuando Shopify falla.
- `python manage.py check`.

## Documentación a actualizar en el mismo cambio

- [`../contracts/API.md`](../contracts/API.md): fila de `GET /api/orders/`.
- [`../apps/orders.md`](../apps/orders.md): sección "Listado para el
  frontend" y pruebas.
- [`../apps/integrations.md`](../apps/integrations.md): `list_orders_page`
  en `shopify/`.
- `integrations/shopify/QUERIES.md`: fila de `LIST_ORDERS_PAGE` con fecha
  y resultado de la introspección y del costo medido.
- [`../architecture/APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md)
  y [`../INDEX.md`](../INDEX.md): `orders` también expone el listado de
  ventas al frontend.
- Este plan: estado y "Notas de implementación" al terminar.

## Notas de implementación

Verificado contra la tienda real (solo lectura) el 2026-10-05:

- **Campos**: todos existen en la API 2024-07. `Customer.email` y
  `Customer.phone` están deprecados, así que el email y el teléfono se leen
  del pedido (`Order.email` / `Order.phone`).
- **Costo** (riesgo 3): 50 pedidos con 30 líneas cuestan 72 puntos (52 con
  20). El cálculo del plan estaba muy sobreestimado; el máximo de 50 es
  seguro.
- **Pedidos de más de 60 días** (riesgo 2): el token los devuelve; no falta
  el scope `read_all_orders`.
- **Etiquetas** (riesgo 1): la búsqueda `tag:` no distingue mayúsculas, y
  la detección local tampoco. Los pedidos viejos de `pamo_web` llevan
  `SODIMAC` y algunos de Falabella `Falabella` (con `B2C` o
  `Astroselling`): se reconocen. Los de Addi (`Addi-Marketplace`) y
  cotizaciones no son de un marketplace de `Marketplace`: salen como
  `shopify`.
- **Nombres**: las órdenes de esta tienda no llevan `#` (`20131`). `search`
  quita el `#` y busca `name:"20131"`, con coincidencia exacta verificada.
- **Fechas**: se envían como `created_at:>='2026-10-03T00:00:00-05:00'`;
  el resultado cae dentro del día en hora de Colombia.
- **`id:A OR id:B`** funciona y se combina con otros filtros entre
  paréntesis.
- **`total`** es `totalPriceSet` de Shopify (lo que pagó el cliente, con
  envío e impuestos), así que puede ser mayor que la suma de `line_total`.
  En `orders_not_created` sí es la suma de las líneas locales.

Cambios respecto al diseño:

- Agregado el 2026-10-05 a pedido del usuario: `fulfillment` (bodega de
  despacho) en las dos listas, leído tal cual de `MarketplaceOrder`
  (`fulfillment_status`, `_location_id`, `_location_name`, `_note`). Con
  `novedad`, la bodega va vacía y `note` trae el motivo. Un pedido sin fila
  local lo lleva vacío. En `orders_not_created` casi siempre está vacío,
  porque la bodega se elige después de resolver los SKU.

- Se agregó `orders/functions/order_listing_format.py` con lo que comparten
  las dos listas (comprador local, importes, rango de días).
- El marketplace se toma primero de la fila local y, si no hay, de la
  etiqueta.
- Se agregó la migración `0012_shopify_order_id_index`.
- Riesgos 4 (latencia) y 5 (reembolsos: se usa `quantity` original) siguen
  abiertos.
