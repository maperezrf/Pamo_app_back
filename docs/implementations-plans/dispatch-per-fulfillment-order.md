# Plan: un despacho por bodega cuando Shopify divide el pedido

Estado: **fase 1 implementada en código (2026-10-16), sin desplegar**; fase
2 pendiente. D1 y D2 confirmadas. Lo vigente de la fase 1 ya está en
`docs/apps/orders.md` ("Despacho a bodegas") y `docs/contracts/API.md`.
Diferencias con el diseño de abajo: la división lee la copia local de
fulfillment orders (sin lectura en vivo: el webhook la refresca justo antes
de crear el despacho); la descarga de la guía no crea despachos y responde
`400` si falta `dispatch_id`; las acciones responden `dispatch` y
`dispatches`. Mientras falte la fase 2, "Notificar" en un despacho `split`
responde `400` y el proceso lo salta. Rol: arquitecto
([`../roles/ARCHITECT.md`](../roles/ARCHITECT.md)). Caso que lo origina: pedido
web **#20577**.

## Objetivo

Cuando Shopify reparte un pedido en varias bodegas (varios *fulfillment
orders* abiertos), cada bodega tiene su propio despacho en el panel:

1. Botón **"Generar guía"** por bodega, con origen en esa bodega y solo con
   sus productos.
2. Botón **"Notificar a proveedor"** por bodega, que avisa solo a esa bodega
   por sus canales y deja **su** guía en **su** fulfillment order de Shopify.

Un pedido que sale de una sola bodega no cambia.

## Estado actual verificado

- `Dispatch.shopify_order` es `OneToOneField` (`orders/models.py:420`): un
  despacho por pedido, una bodega, una guía.
- #20577 (tienda web, `UNFULFILLED`) en la base de Railway, leído el
  2026-10-16:
  - Shopify lo partió en dos fulfillment orders `OPEN`: **Baru**
    (`CB8080L`, `S2001-120`) y **Bodega Envia** (`YW617/2`, `YW617/3`).
  - Nuestro `Dispatch` está en `manual`, sin bodega: "Ninguna bodega tiene
    el pedido completo" (`select_fulfillment_location` busca una sola
    bodega para todo). No tiene avisos.
- `fulfill_in_shopify` (`orders/functions/shopify_fulfillment.py:66`) rechaza
  a propósito un pedido con varios fulfillment orders abiertos ("cumplir a
  mano").
- La guía (`dispatch_actions._shipping_payload`) y el aviso
  (`dispatch_details`) usan **todas** las líneas del pedido
  (`order.lines`). La referencia en Envía es `pamo-<número>`, una por pedido.
- El aviso por API de Envía (`notify_dispatch._notify_envia`) busca la orden
  por nuestro identificador y por el número de Shopify. Con dos despachos, el
  segundo encontraría la orden del primero.
- La copia local `ShopifyFulfillmentOrder` (con líneas) ya existe y está
  poblada (2252 filas), pero se borra y se recrea en cada `upsert`: **no se
  le puede apuntar con FK**.
- Configuración de bodegas en Railway (2026-10-16): **solo Bodega Envia
  tiene canal de aviso** (API, `envia_warehouse_id` 311). Baru, Boccherini,
  Taumm y las demás no tienen canales. Bodega Envia tiene
  `creates_own_label=False`, aunque el expediente dice que es la bodega que
  crea su propia guía.

## Decisiones (confirmadas por el usuario el 2026-10-16)

- **D1. Cuándo se divide.** Solo en pedidos de la **tienda web** (también
  Addi y cotizaciones) que tengan **más de un fulfillment order abierto** en
  bodegas distintas. En ese caso se sigue la división de Shopify, sin
  recalcular inventario. Así se respeta lo que Shopify ya reservó (en #20577
  nuestro inventario ponía YW617 en Boccherini y Shopify lo puso en Bodega
  Envia). Los pedidos de marketplace, **por ahora**, siguen con una bodega
  elegida por la importación (decisión E: un envío = una guía).
- **D2. Contrato.** `dispatch` (objeto) pasa a `dispatches` (lista): un
  pedido puede tener varios despachos. Backend y frontend se despliegan
  juntos.

## Diseño

### 1. Modelo (`orders/models.py`, migración nueva)

- `Dispatch.shopify_order`: `OneToOneField` → `ForeignKey`, con
  `related_name="dispatches"`.
- Restricciones:
  - `UniqueConstraint(shopify_order, location)`;
  - `UniqueConstraint(shopify_order, condition=Q(location__isnull=True))`
    (en Postgres los `NULL` no chocan entre sí, y un pedido no debe tener dos
    despachos sin bodega).
- Campo nuevo `split` (bool, por defecto `False`): el despacho es una parte
  de un pedido dividido. Decide qué productos lleva y cómo se cumple en
  Shopify.
- La migración conserva los despachos actuales tal cual: hoy todos son de
  una sola bodega.

### 2. Creación (`dispatch_orders.py`)

- Helper nuevo `shopify_split_locations(order)`. Devuelve las
  `DispatchLocation` de los `ShopifyFulfillmentOrder` locales que no estén
  en `CLOSED_STATUSES`, sin repetir. La división aplica solo si hay más de
  una **y** el pedido es de la tienda web (D1).
  - Un fulfillment order en una bodega que no está registrada → un despacho
    `manual` para esa parte, con el motivo, como hoy.
- `_create(order)` → `_create_dispatches(order)`:
  - pedido dividido: un `Dispatch(split=True, location=…)` por bodega, en
    `esperando_guia`;
  - si no: igual que hoy (`assign_dispatch_location`).
- `ensure_dispatch(order)` → `ensure_dispatches(order)`, que devuelve una
  lista. `assign_web_dispatch` y el comando `assign_web_dispatches` pasan a
  usarlo.
- `reopen_manual_dispatch`: si el pedido tiene **un solo** despacho, `manual`,
  sin bodega y **sin avisos**, y Shopify ya lo dividió, se borra y se crean
  los despachos por bodega. Así se arregla #20577 con el primer clic. Un
  despacho con avisos nunca se borra.
- `process_order_dispatch(order)`: recorre los despachos del pedido con un
  `process_dispatch(dispatch)` nuevo (el cuerpo de hoy, recibiendo el
  despacho). El estado que devuelve por pedido es el "peor" de sus despachos;
  el resumen del proceso cuenta por despacho.
- `_candidates`: los filtros `dispatch__…` pasan a `dispatches__…`, con
  `.distinct()`.

### 3. Productos del despacho (helper nuevo, `dispatch_details.py`)

`dispatch_lines(dispatch)` decide qué productos lleva cada despacho:

- si `split=False`: `order.lines`, como hoy;
- si `split=True`: las líneas del `ShopifyFulfillmentOrder` local de esa
  bodega. El precio unitario se toma de la `ShopifyOrderLine` con el mismo
  SKU, porque el fulfillment order no lo trae.

Lo usan `dispatch_details` (que pasa a recibir el despacho: correo,
WhatsApp, productos para Envía) y `_shipping_payload` (contenido del paquete
y `declaredValue` = suma de esas líneas). El campo `total` del aviso queda
como el total de esas líneas.

### 4. Acciones del panel (`dispatch_actions.py`, `apis.py`)

- Todas las funciones reciben el **despacho**, no el pedido:
  `quote_dispatch_label`, `generate_dispatch_label`, `fetch_channel_label_now`,
  `envia_label_file` y `notify_dispatch_now` (esta llama a
  `process_dispatch`).
- Endpoints: las mismas URLs, con un `dispatch_id` opcional (en el cuerpo de
  los `POST` y como query en el `GET` del archivo):
  - pedido con un despacho: no hace falta, compatible con hoy;
  - pedido con varios y sin `dispatch_id`: `400`;
  - `dispatch_id` que no es de ese pedido: `404`.

  Helper único en `apis.py` para resolver el despacho. Permisos sin cambio
  (`Admin`/`Operaciones`).
- Referencia de la guía en Envía: `pamo-<número>`; en un despacho dividido,
  `pamo-<número>-<id del despacho>`, para que cada guía tenga la suya y el
  aviso de "resultado incierto" apunte a la correcta. Lo mismo en el nombre
  del archivo que se descarga.

### 5. Aviso por API de Envía (`notify_dispatch._notify_envia`)

- Identificador en un despacho dividido: `<número>-<id del despacho>`.
- La búsqueda por número de Shopify (la orden que Envía importó de la tienda
  conectada) se mantiene. Pero no vincula una orden de Envía que ya sea el
  `external_id` de un aviso `api` de **otro** despacho del mismo pedido. Así
  dos bodegas con API nunca comparten orden.

### 6. Guía en Shopify (`shopify_fulfillment._fulfill`)

En un despacho con `split=True`, se cumple solo el fulfillment order abierto
**en la bodega del despacho**, sin mover nada:

- si no hay ninguno abierto en esa bodega, pero alguno ya tiene esta guía →
  se marca hecho;
- si no → error en `shopify_fulfillment_error`, como hoy.

Con `split=False` no cambia (incluido el rechazo de "repartido en varias
bodegas").

### 7. Otros usos de "un despacho por pedido"

- `list_orders.py`:
  - `select_related("dispatch__location")` pasa a
    `prefetch_related("dispatches__location", "dispatches__notifications")`,
    con `defer` de `label_pdf` usando un `Prefetch` con queryset;
  - `format_dispatch` devuelve la lista `dispatches`;
  - `_fulfillment_from_dispatch`: con varias bodegas, `location_name` lleva
    los nombres separados por coma.
- `sodimac_rpa.py:278` (`filter(...).exists()`): ya funciona con FK, no
  cambia.
- `admin.py`: revisar si el admin de `ShopifyOrder` o `Dispatch` usa el
  acceso inverso `order.dispatch`.

### 8. Frontend (`frontend/src/modules/orders/`)

- `OrderExpandedDetail.jsx`: un recuadro de despacho por cada elemento de
  `dispatches`, con su bodega en el título y sus productos.
- `DispatchActions.jsx`: recibe el despacho y manda su `id` en cada acción.
- `OrdersPage.jsx` y `orderFormat.js`: el estado de la columna toma el peor
  estado entre los despachos.

## Contrato (`docs/contracts/API.md`)

- `GET /api/orders/`: `dispatch` → `dispatches` (`[]` si el proceso no lo
  ha tomado). Cada elemento trae lo de hoy, más `id`, `location_id`, `split`
  e `items` (`[{sku, name, quantity}]`, los productos de ese despacho).
- Endpoints `.../dispatch/...`: `dispatch_id` opcional (`400` si el pedido
  tiene varios y falta; `404` si no es del pedido). La cotización devuelve
  la ruta del despacho elegido.

## Riesgos

- **Baru no tiene canales de aviso.** En #20577, "Notificar" en la parte de
  Baru va a quedar `manual` ("no tiene canales de aviso configurados") hasta
  que se le configure correo o WhatsApp en el admin. Es configuración, no
  código, pero el usuario lo va a ver el primer día.
- **Bodega Envia y la tienda conectada.** Un pedido web tiene despacho en
  Shopify, así que la app de Envía puede importarlo **completo** (con los
  productos de Baru) por la conexión de la tienda. Hay que verificar en
  Envía qué importó para #20577 antes de avisar: si trae las 4 líneas, el
  vínculo por número le pasaría a Bodega Envia productos que no despacha.
  Si es así, la parte de Bodega Envia no debe vincular por número (dejar
  solo el identificador propio). Revisarlo a mano en Envía con #20577.
- `creates_own_label` está en `False` para Bodega Envia, al revés de lo que
  dice el expediente. Confirmar con operaciones el valor correcto; decide si
  la parte de Bodega Envia lleva "Generar guía".
- La copia local de fulfillment orders puede estar vieja si mover un
  fulfillment order no dispara `orders/updated` (riesgo abierto de
  [`order-fulfillment-orders-view.md`](order-fulfillment-orders-view.md)).
  Mitigación: al crear los despachos divididos, y en `_fulfill`, leer en vivo
  con `get_fulfillment_orders`; la copia local solo se usa para mostrar y
  para los productos. Si en vivo no coincide con lo local, el despacho va a
  `manual` con el motivo.
- Pasar de `OneToOne` a `FK` cambia el acceso inverso (`order.dispatch` →
  `order.dispatches`). Cualquier uso que se escape falla en ejecución, no en
  `check`. Verificar con grep de `\.dispatch\b` y `dispatch__` en `orders/`
  y en las pruebas.

## Pruebas (sin red, `orders/tests_dispatch.py`)

- Pedido web con dos fulfillment orders abiertos → dos despachos
  `split=True`, uno por bodega, cada uno con sus productos. Uno solo → un
  despacho, como hoy. Marketplace con varios → no se divide.
- Despacho `manual` sin bodega ni avisos, en un pedido ya dividido → el
  botón lo reemplaza por los despachos por bodega. Con avisos no se toca.
- Cotizar y generar guía con `dispatch_id`: origen en la bodega del
  despacho, paquete con sus productos y valor, referencia con sufijo. Sin
  `dispatch_id` en un pedido dividido → `400`; un id de otro pedido → `404`.
  Con un solo despacho, sin `dispatch_id` → igual que hoy.
- Notificar un despacho: avisa solo a los canales de su bodega, con sus
  productos. El otro despacho no se toca.
- Envía: el segundo despacho no vincula la orden que ya es `external_id`
  del primero.
- Shopify: `split=True` cumple solo el fulfillment order de su bodega y no
  llama a `move_fulfillment_order`. `split=False` sigue rechazando varias
  bodegas.
- Listado: `dispatches` como lista, con consultas fijas por página
  (`assertNumQueries`).
- Permisos de los endpoints: `403` sin rol (ya existen; se agrega el caso
  con `dispatch_id`).
- `python manage.py check`, `python manage.py makemigrations --check` y
  `python manage.py test orders`.

## Entrega sugerida (planes pequeños)

1. **Fase 1**: modelo y migración, creación dividida, productos por
   despacho, "Generar guía" y "Descargar guía" con `dispatch_id`, listado
   `dispatches` y frontend.
2. **Fase 2**: "Notificar a proveedor" por despacho, aviso Envía sin
   compartir orden y guía en Shopify por fulfillment order.

Cada fase se despliega con su frontend.

## Documentos a actualizar

- `docs/apps/orders.md`, sección "Despacho a bodegas": un despacho por
  bodega en pedidos web divididos, `split`, referencia con sufijo y
  `fulfill_in_shopify` por fulfillment order. Quitar también los enlaces a
  `order-dispatch-to-warehouses.md` y `split-dispatch-by-warehouse.md`, que
  ya no existen en `docs/implementations-plans/`. Lo mismo en
  `docs/architecture/APP_BOUNDARIES.md` y en
  `order-fulfillment-orders-view.md`.
- `docs/contracts/API.md` (`dispatches`, `dispatch_id`).
- En el frontend: `docs/apps/orders.md` y
  `docs/contracts/API_CONSUMPTION.md`.

Al terminar las dos fases, este plan se borra.
