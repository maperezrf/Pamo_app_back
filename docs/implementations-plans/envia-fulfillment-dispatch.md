# Plan: despachos de Mercado Libre y Falabella a Envía Fulfillment con su guía

**Subplan de [`order-dispatch-to-warehouses.md`](order-dispatch-to-warehouses.md)**
(2026-10-07): ninguna tienda quedará conectada a Envía y Envía es una bodega
más, notificada por API. Este documento conserva la evidencia y el avance
de la parte Envía; las decisiones de alcance las manda el plan general.

Estado: **en curso (2026-10-07)**. Fase 0 hecha en lectura y fase 1 hecha
salvo el cruce SKU → `variantId` y la búsqueda por identificador (ver
"Avance"). Sin escrituras en Envía. Pendiente de las decisiones marcadas
como "abierta".

## Avance

- 2026-10-07 — **Guías de los canales** (fase 1, parcial):
  `integrations/mercadolibre/functions/get_shipment_label.py`
  (`get_shipment_label`, `is_label_available`; `MercadoLibreClient.get_bytes`
  y `substatus` en `get_shipment`) e
  `integrations/falabella/functions/get_shipping_document.py`
  (`get_shipping_document`), con pruebas sin red. Prueba real, una vez por
  canal: Mercado Libre, envío `cross_docking` en `ready_for_pickup` (ya
  impreso, no cambió de subestado) → PDF de 2 páginas, 51.031 bytes;
  Falabella, pedido con paquete y guía Servientrega → PDF de 1 página,
  260.580 bytes, guía real con 3 copias. Los PDF no se guardan en el
  proyecto.
- Hallazgo: la guía de Falabella trae el correo y el teléfono reales del
  comprador, que `GetOrder` no entrega.
- 2026-10-07 — **`integrations/envia_fulfillment/`** (fase 1): cliente con
  escrituras bloqueadas sin `ENVIA_FULFILLMENT_WRITES_ENABLED`, y
  `list_warehouses`, `find_order`, `get_order`, `create_order` (con la guía
  en `shipments`), `add_order_tracking`, `list_shop_products`. Pruebas sin
  red. **Ninguna escritura real**: `create_order` y `add_order_tracking`
  no se han llamado contra Envía.
- 2026-10-07 — **Fase 0 (solo lectura)** con el token real:
  - `GET /auth/me`: empresa `2156`, tienda principal `1631` "Pamo.co"
    (Shopify `prueba-1615`, que es la tienda de producción).
  - Tiendas conectadas (filtros del listado): "Pamo.co" (Shopify), "Pamo"
    (Mercado Libre) y `sellercenter.falabella.com/SC6CB98` (Falabella).
  - Bodegas: `311` "Bogotá CO " y `224` "Lastmile Bogotá".
  - 12.361 órdenes. La integración nativa de Mercado Libre crea la orden
    con `identifier` = número de pedido de ML (ej. `2000015399650969`) y
    un `shipments[]` con `trackingNumber` `MEL<shipment_id>…` y `label`.
    La tienda Shopify también importa sus pedidos (`identifier` = número
    de Shopify, ej. `20399`).
  - Los ítems de una orden traen `variantId` de Envía, `sku` y `ecartId`
    (id del canal): el `variantId` es por tienda (el SKU `I001` de la
    tienda de Mercado Libre es la variante `215377`).
  - El listado exige el header `timezone` (no como parámetro; se manda
    `America/Bogota`) y que `search` sea un objeto, pero ninguna clave
    probada filtra: `find_order` falla con
    `ENVIA_FULFILLMENT_SEARCH_UNRELIABLE` en vez de decir "no existe".
  - `list_shop_products` supera 4 MB (catálogo completo sin paginar).
  - **Riesgo nuevo**: si Envía importa a la vez los pedidos de Shopify y
    los del canal, un mismo pedido de Mercado Libre podría estar dos veces
    (una por tienda). Falta confirmarlo con un pedido concreto.

## Contexto

Envía Fulfillment opera una bodega propia ("Bodega Envia" en Shopify). Hoy
las integraciones nativas de Mercado Libre y Falabella crean sus pedidos
directamente en Envía, con la guía del canal adjunta, y Envía despacha con
esa guía.

Se quiere que esos pedidos pasen **por nuestro backend**: que `orders` cree
la orden en Envía Fulfillment, con la guía que descarga del canal. Así el
paso queda bajo el mismo control que la creación en Shopify (idempotencia,
reclamo, estados y errores visibles).

Sodimac y Madecentro tienen otro flujo de guía y quedan fuera.

## Objetivo

Para cada envío de Mercado Libre o Falabella que debe salir de la bodega de
Envía:

1. esperar a que el canal tenga la guía disponible;
2. descargar el PDF de la guía;
3. crear **una** orden en Envía Fulfillment con bodega, productos, dirección
   y la guía en `shipments[]`;
4. guardar el resultado (id de la orden en Envía, número de guía, estado o
   error) para verlo y reintentar.

Fuera de alcance: generar o comprar guías (API de Shipping de Envía),
Sodimac, Madecentro, cancelar desde el frontend, devoluciones e inventario.

## Evidencia verificada

| Hecho | Fuente |
| --- | --- |
| Envía Fulfillment crea la orden con la guía: `POST /company/{companyId}/client/order/create` con `shipments: [{trackingNumber, trackingUrl, label: {name, type, raw}}]`; `raw` es el archivo en base64 (`application/pdf`). Campos obligatorios: `warehouseId`, `orderIdentifier`, `ecommerceStatus`, `total`, `products[{variantId, quantity, price}]`, `currency`, y `customerId` **o** `shippingAddress` + `email`. | Colección oficial `api.fulfillment.envia.com`, leída el 2026-10-07. |
| La guía también puede agregarse después (`POST …/client/order/{orderId}/tracking-numbers`) y corregirse (`PUT …/tracking/{pretrackingId}`). | Idem. |
| Buscar una orden por identificador: `POST …/client/order/search` con `{"identifier", "shopId"}`. Cancelar: `PUT …/client/order/cancel` con `{"orders": [ids]}`; existe `…/uncancel`. | Idem. |
| Productos y bodegas: `GET …/client/shop/{shopId}/products`, `GET /client/products/{productId}/variants`, `GET …/warehouse/client`. | Idem. |
| Mercado Libre entrega la guía: `GET /shipment_labels?shipment_ids=<id>&response_type=pdf`, en `ready_to_ship` con `ready_to_print`, `printed` o `ready_for_pickup`. Devolvió un PDF válido de 52.200 bytes el 2026-09-10. Full no aplica. | `pamo-one-engineering/docs/modules/orders/guide-readiness/20260909-mercadolibre-label/HANDOFF.md`. |
| Descargar la etiqueta de Mercado Libre pasa el envío de `ready_to_print` a `printed` (efecto real, como imprimir desde el panel). | Idem. |
| Falabella entrega la guía: `GetDocument` con `DocumentType=shippingParcel` y los `OrderItemIds` del paquete; responde `Body.Documents.Document` con el PDF en base64. Solo existe con paquete (`PackageId` en los ítems). Leerla no cambia el estado. | `pamo-one-engineering/apps/api/orders/falabella_guides.py` y `docs/modules/orders/guide-readiness/20260909-beta-integration/HANDOFF.md`. |
| Mercado Libre informa el origen del envío (`origin.node.node_id`; hoy Kennedy, Itagüí y Cota). Falabella trae un solo `originNode`. | Lectura real de envíos, 2026-10-07. |
| Referencia de un cliente Envía Fulfillment completo (lista de 162 operaciones, escrituras apagadas por defecto, reintentos acotados). | `pamo-maestro/docs/ENVIA-FULFILLMENT.md`, `apps/pamo-maestro-api/src/envia.js`. |

**No verificado** (se resuelve en la fase 0): la forma real de una orden que
ya creó la integración nativa; el formato de `GET …/client/orders`; la
paginación y el filtro por SKU de los productos; si Envía rechaza un
`orderIdentifier` repetido.

## Decisiones

| Tema | Decisión | Estado |
| --- | --- | --- |
| Dónde vive el transporte | `integrations/envia_fulfillment/`, separado de `integrations/envia/`: otra API, otro host y otro token (ver nota de credenciales). | Propuesta |
| Dueño del flujo | `orders`: es parte del pedido de marketplace, igual que la creación en Shopify y la bodega de despacho. | Propuesta |
| Qué envíos van a Envía | Solo los asignados a la bodega de Envía. **Abierta**: en Mercado Libre, ¿manda el nodo de origen de ML o nuestra `fulfillment_location`? Hoy pueden no coincidir. Operaciones debe decir qué nodo de ML es la bodega de Envía. | Abierta |
| Momento | Después de que la orden está creada en Shopify y la guía está disponible. Proceso programado cada pocos minutos, no en el webhook. | Propuesta |
| Cliente en Envía | `shippingAddress` + `email`, sin crear clientes en Envía. **Abierta**: Mercado Libre casi nunca da correo; ¿se usa un correo fijo por canal? | Abierta |
| `orderIdentifier` | `<marketplace>-<número>` (el mismo que la etiqueta de Shopify, ver "Idempotencia" en `../apps/orders.md`). | Propuesta |
| Integraciones nativas | Se apagan **por canal** antes de activar el nuestro, para no duplicar órdenes en Envía (lección de Falabella en Shopify, 2026-10-05). | Requisito |
| Paquete de Falabella | El paquete (y su guía) lo crean hoy Seller Center u operaciones. Fase 1 **no** llama `SetStatusToReadyToShip`; espera el paquete. **Abierta**: quién lo crea cuando se apague el conector nativo. | Abierta |

## Diseño

### `integrations/envia_fulfillment/`

Patrón de [`../architecture/INTEGRATIONS.md`](../architecture/INTEGRATIONS.md)
y [`../patterns/EXTERNAL_CLIENTS.md`](../patterns/EXTERNAL_CLIENTS.md).

- `client.py`: `EnviaFulfillmentClient` con token Bearer, `companyId`,
  timeout, sin redirecciones, tamaño máximo de respuesta y error propio
  (`EnviaFulfillmentAPIError`). Reutilizar de `integrations/envia/client.py`
  la validación de respuestas: Envía a veces responde HTTP 200 con un error
  dentro del cuerpo. Bloquea toda escritura si
  `ENVIA_FULFILLMENT_WRITES_ENABLED` es falso.
- `functions/`, una por operación: `list_warehouses`, `list_shop_products`
  (con variantes), `search_order(identifier)`, `get_order(order_id)`,
  `create_order(...)` y `add_order_tracking(...)`. `cancel_orders` queda para
  una fase posterior.
- `tests.py` sin red. `NoNetworkTestRunner` ya lo exige.

### Guías de los canales

Funciones nuevas en la integración de cada canal. Devuelven
`{"tracking_number", "pdf_bytes"}` o `None` si la guía aún no existe:

- `integrations/mercadolibre/functions/get_shipment_label.py`: GET
  `/shipment_labels`. Requiere un método del cliente que devuelva bytes;
  hoy `MercadoLibreClient.get` solo devuelve JSON. Valida que sea un PDF
  real (firma `%PDF`, tamaño máximo) y no convierte HTML o JSON en PDF.
- `integrations/falabella/functions/get_shipping_document.py`:
  `GetDocument` con `shippingParcel`. El número de guía sale de
  `TrackingCode` de los ítems.

Portar la lógica de `pamo-one` (`mercadolibre_labels.py`,
`falabella_guides.py`) en lo que aplique, sin su capa de tenants ni ledger.

### `orders`

- Modelo nuevo `EnviaFulfillmentDispatch`, uno por envío (un pack de Mercado
  Libre son varias `MarketplaceOrder`):
  - relación con las `MarketplaceOrder` del envío;
  - `identifier` (único), `status` (`pending`, `waiting_label`,
    `processing`, `created`, `error`), `envia_order_id`, `tracking_number`,
    `label_sha256` (no se guarda el PDF), `error_description` y fechas.
- `orders/functions/dispatch_to_envia_fulfillment.py`, proceso
  `orders.dispatch_envia_fulfillment` en
  `orchestrator/registrations.py` (`allow_concurrent=False`, programado):
  1. Envíos elegibles: canal Mercado Libre o Falabella con el canal activado
     en configuración, orden de Shopify creada, bodega de Envía, sin
     despacho creado. Mercado Libre: no Full y no cancelado.
  2. Reclamo atómico del despacho (`processing`), igual que
     `claim_orders`.
  3. `search_order(identifier)`: si ya existe en Envía, se vincula sin
     crear otra.
  4. Guía del canal. Sin guía todavía → `waiting_label` y se reintenta en
     la próxima corrida.
  5. Productos: SKU de Shopify (`products.resolve_marketplace_sku` y kits
     ya resueltos en `MarketplaceOrderItem`) → `variantId` de Envía.
     Sin variante → `error` con el SKU.
  6. `create_order` con `shipments[0].label` = PDF en base64,
     `ecommerceStatus="paid"`, `currency="COP"`.
  7. `created` con `envia_order_id` y `tracking_number`.
  8. Si falla la red al crear, queda en `processing` para revisión manual,
     igual que en Shopify.
- Listado: agregar el estado del despacho a Envía en `GET /api/orders/`
  (contrato nuevo, aditivo), para verlo en el acordeón del frontend.

### Configuración (`config/constants.py`)

`ENVIA_FULFILLMENT_API_TOKEN`, `ENVIA_FULFILLMENT_COMPANY_ID`,
`ENVIA_FULFILLMENT_SHOP_ID`, `ENVIA_FULFILLMENT_BASE_URL` (sandbox
`fulfillment-api-test.envia.com` o producción),
`ENVIA_FULFILLMENT_WAREHOUSE_ID`, `ENVIA_FULFILLMENT_WRITES_ENABLED`
(falso por defecto) y `ENVIA_FULFILLMENT_CHANNELS` (canales activados,
vacío por defecto).

**Credenciales**: el token de Envía que existe en Railway (servicio
`pamo_web`, URL `apifulfillment.envia.com`) es de esta API de Fulfillment,
no de la de Shipping que usa `integrations/envia/`. Hay que confirmar a qué
empresa pertenece y obtener `companyId` y `shopId` antes de la fase 0.

## Fases

| Fase | Qué | Escrituras externas |
| --- | --- | --- |
| 0. Reconocimiento | Con el token: `GET /auth/me`, bodegas, una página de productos y **una orden creada por la integración nativa** de cada canal (`get_order`). Confirmar la forma real de `shipments`/`trackings`, el identificador que usan y el `warehouseId`. Ajustar este plan. | No |
| 1. Lecturas en código | Cliente, funciones de lectura y las dos funciones de guía, con pruebas sin red. | No |
| 2. Escritura en sandbox | `create_order` y `add_order_tracking` contra `fulfillment-api-test`, con un PDF de prueba. | Solo sandbox |
| 3. Flujo completo | Modelo, proceso, reclamo e idempotencia en `orders`; contrato del listado. Probado con mocks; corrida manual con `ENVIA_FULFILLMENT_CHANNELS` vacío (no hace nada). | No |
| 4. Piloto | Un canal, con su integración nativa apagada y escrituras activadas, **un** envío con el parámetro `limit=1`. Verificar la orden en Envía y que la bodega la reciba. | Sí, 1 orden |
| 5. Activación | Programar el proceso para ese canal; luego el otro. | Sí |

## Riesgos

- **Duplicados en Envía** si la integración nativa sigue activa. Mitigación:
  activación por canal, `search_order` antes de crear y reclamo atómico.
- **Mercado Libre marca la guía como impresa** al descargarla. Es el flujo
  esperado, pero ya no se ve "pendiente de imprimir" en Mercado Libre.
- **Guía de Falabella** que depende de un paquete que hoy crea otro actor.
- **Bodega equivocada** si el nodo de Mercado Libre y nuestra bodega no
  coinciden (decisión abierta).
- **Cruce de SKU** con los productos de Envía: SKU que existe en Shopify y
  no en Envía, o kits.
- La API de Envía tiene rutas heredadas sin `/company/{companyId}`
  (advertido en `pamo-maestro`); se valida cada una en sandbox.

## Pruebas

Sin red (`NoNetworkTestRunner`):

- cliente: errores HTTP y de cuerpo, bloqueo sin `WRITES_ENABLED`;
- guías: PDF válido, HTML/JSON rechazado, guía no disponible → `None`;
- proceso: elegibilidad por canal y bodega, `waiting_label`, orden
  existente vinculada sin crear, SKU sin variante → error, fallo de red al
  crear → `processing`, pack de Mercado Libre = una orden en Envía,
  reintento sin duplicar;
- API: el estado del despacho en `GET /api/orders/` y su rechazo por
  permisos.

## Documentos a actualizar

`docs/architecture/INTEGRATIONS.md` (proveedor nuevo),
`docs/apps/integrations.md`, `docs/apps/orders.md` (sección nueva y
estados), `docs/contracts/API.md` (campo del listado),
`docs/apps/orchestrator.md` (proceso registrado), `docs/INDEX.md` si cambia
el mapa, y el expediente de pedidos del frontend cuando se muestre el
estado.

## Preguntas para operaciones

1. ¿Qué nodo de Mercado Libre (Kennedy, Itagüí, Cota) es la bodega de Envía?
   ¿Todo lo que sale de ese nodo va a Envía?
2. En Falabella, ¿quién crea el paquete y la guía cuando se apague el
   conector nativo de Envía?
3. ¿Qué correo usar en la orden de Envía cuando el canal no lo da?
4. ¿Se puede apagar la integración nativa de un canal en Envía sin afectar
   pedidos en curso?
