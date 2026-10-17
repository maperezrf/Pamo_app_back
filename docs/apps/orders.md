# App `orders`

`orders/` orquesta la importación de pedidos de marketplaces hacia Shopify:
Falabella, Madecentro y Sodimac por lote programado, y Mercado Libre por
webhook. No es
transporte de proveedor (eso vive en `integrations/`) y no depende de
`customers/`: los pedidos de cada canal se crean en Shopify a nombre de un
**cliente fijo por canal**, y los datos reales del comprador se guardan
aquí para facturar después en Siigo. Los planes de cada canal ya se
cerraron; su contenido vigente está en este documento.

## Capacidades

- `MarketplaceOrder` / `MarketplaceOrderItem`: modelo general (no
  específico de Falabella) que registra cada pedido de marketplace, sus
  ítems, y el resultado de intentar llevarlo a Shopify. El campo
  `marketplace` (`TextChoices`) es lo que distingue el canal — un
  marketplace nuevo agrega un valor ahí, no una tabla nueva. La lista vive
  en `products.models.Marketplace`; `MarketplaceOrder.Marketplace` es un
  alias (ver [`products.md`](products.md)).
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
     Falabella** sin orden y en `pending` / `error_creando_orden`, lo
     reclama con `claim_orders` y llama `process_shipment([order],
     cliente_fijo)`. Desde 2026-10-05 reclama como los demás canales: antes
     reintentaba todo lo que no tuviera `shopify_order_id`.
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
  de un pack), resuelve SKUs, elige **una** bodega para todo el envío y
  crea **una** orden de Shopify con todas las líneas. El resultado
  (estado, `shopify_order_id`, bodega) queda igual en cada orden del envío.
- **Resolución de SKU** en `process_shipment`:
  1. Traduce el SKU del marketplace con el catálogo de `products`
     (`resolve_marketplace_sku(order.marketplace, sku)`, ver
     [`products.md`](products.md)).
     - Si hay equivalencia a un producto simple, usa el SKU de Pamo del
       producto (ej. Madecentro `PMO-028-MP` → `PAC8424`).
     - Si no hay equivalencia, usa el SKU tal cual llega. Hoy Falabella y
       Mercado Libre no tienen equivalencias cargadas.
     - **Si la equivalencia es un kit**, va una línea por componente, con
       el precio repartido en partes iguales por unidad de componente
       (`products.expand_product`). El ítem guarda `shopify_variant_id`
       vacío y un `inventory_snapshot` con un elemento por componente
       (`sku`, `quantity`, `variant_id`, `locations`). Un kit sin
       componentes deja el envío en `error_creando_orden`.
  2. Busca cada SKU resultante en Shopify
     (`integrations.shopify.get_variant_inventory_by_sku`), en vivo y
     siempre, aunque el variant id esté cacheado.
  3. Si no existe, el error nombra los dos SKU (`SKU-MKT (equivalencia
     SKU-PAMO)`, o `SKU-MKT (componente X del kit K)`).

  `MarketplaceOrderItem.marketplace_sku` conserva siempre el SKU del
  marketplace.
- `claim_orders(marketplace, order_ids)` (`orders/functions/claim_orders.py`):
  **reclamo atómico** compartido por Falabella, Mercado Libre, Madecentro
  y Sodimac. Pasa a
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

### Decisiones de Mercado Libre (A–G)

Confirmadas por el usuario el 2026-09-24; el código las cita por letra.

- **A. Solo pedidos pagados.** Un aviso de un pedido no pagado se ignora
  sin guardar; el de "pagado" llega después (la orden va `PAID` a Shopify).
- **B. Webhook sin firma.** Mercado Libre no firma: el payload nunca se usa
  como dato (solo el id; el pedido se lee de la API con el token propio) y
  se valida tópico, recurso, app y cuenta; si no cumple, `200` sin proceso.
- **C. Cliente fijo** del canal (`MERCADOLIBRE_SHOPIFY_CUSTOMER_ID`).
- **D. Full se omite** (`logistic_type == "fulfillment"`): despacha Mercado
  Libre desde su bodega.
- **E. La unidad de despacho es el envío**: un envío = una guía = una sola
  bodega = una orden de Shopify con todas las líneas. Las órdenes que
  comparten `shipment_id` se procesan juntas y solo cuando el envío está
  completo (pack con todas sus órdenes pagadas y cantidades por publicación
  iguales a `shipping_items`). Sin bodega que cubra todo → novedad, y la
  orden se crea igual.
- **F. Concurrencia**: reclamo atómico de todas las órdenes del envío
  (`claim_orders`, `procesando`). Una fila que se queda en `procesando` no
  se reintenta sola: se revisa a mano.
- **G. Recuperación** (`orders.recover_mercadolibre`): avisos no entregados
  (`missed_feeds`), procesos fallidos tras responder `200` (marcadores en
  `pending` con error) y pedidos en `error_creando_orden`. Reporta packs
  incompletos hace más de 6 h.

## Madecentro (rutina por API + webhook en captura)

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

## Sodimac (cola por API)

Migrado desde `pamo_web` el 2026-10-01: las OC nuevas se crean y facturan
aquí; `pamo_web` ya no crea órdenes y solo termina de facturar sus OC
antiguas (convivencia, abajo).

- **Rutina** `orders.sync_sodimac`
  (`orders/functions/sync_sodimac_orders.py`, `allow_concurrent=False`, se
  programa por la API del orquestador):
  1. Reinyecta las OC de Sodimac abiertas (`marketplace_status` distinto de
     `4-ESTADO FINAL`). Reinyectar es la única forma de conocer el estado
     actual de una OC.
  2. Lee la cola (`TipoOrden` `1` y `4`). **La cola es destructiva**: cada
     OC se guarda apenas se lee. OC nueva → fila e ítems
     (`unit_price` = `COSTO_SKU` sin IVA); OC conocida → solo
     `marketplace_status` (con `update`, nunca `save()`).
  3. Crea en Shopify las OC sin orden con `claim_orders` +
     `process_shipment([oc], SODIMAC_SHOPIFY_CUSTOMER_ID, financial_status="PENDING")`.
     Sodimac paga a crédito: **es el único canal que no queda `PAID`**.
  4. Llama a `pamo_web` (`integrations.pamo_web.run_sodimac_invoicing`)
     mientras dure la convivencia (ver abajo).
  - `params={"limit": N}` limita las OC creadas en Shopify, no la lectura.
  - Un fallo puntual no detiene el resto; al final la ejecución termina en
    error con el resumen (incluye OC con error de Shopify).
- **Campos genéricos** de `MarketplaceOrder`, vacíos en los demás canales:
  `marketplace_status` (`ESTADO_OC`) y `marketplace_created_at`
  (`FECHA_TRANSMISION`, ISO o día primero `DD/MM/AAAA[ HH:MM[:SS]]`).
- **Comprador y entrega** (desde el 2026-10-11): `get_orders` trae por OC
  `delivery` (`CLIENTE`, `CEDULA`, `EMAIL_CLIENTE`, `TELEFONO_CLIENTE`,
  `DIRECCION_ENTREGA`, `CIUDAD_ENTREGA`, `DEPARTAMENTO_ENTREGA`) y se guarda
  en los campos `customer_*` de `MarketplaceOrder` (nombre: primera palabra
  como nombre, el resto como apellido). Lo usa el aviso a Envía. Las OC
  guardadas antes se completan en la siguiente reinyección.
- **Nota en Shopify**: `"Sodimac #<OC> — <comprador> CC <cédula>"`, como en
  los demás canales. Etiqueta `sodimac`.
- **Guía** (rótulo + relación de TCC): Sodimac no la entrega por API. La
  genera el **RPA de guías** (repositorio `maperezrf/rpa_sodimac`, servicio aparte en
  Railway; Selenium sobre el portal `hub.homecenter.co`). Desde el
  2026-10-11 lo usa este backend en vez de `pamo_web`
  (`orders/functions/sodimac_rpa.py`, `integrations/sodimac_rpa/`):
  1. **Pausado desde 2026-10-15 mientras se prueba el RPA**: el sync no
     pide guías (llamada comentada en `sync_sodimac_orders.py`, resumen
     "pausado"; las dos pruebas del sync con el RPA están en `skip`). Hoy
     solo se piden desde el panel o el admin. Al reactivar: descomentar y
     quitar los `skip`.
     Al final de `orders.sync_sodimac` (paso 3b), `request_sodimac_labels`
     manda al RPA (`POST {SODIMAC_RPA_URL}/trigger`) las OC con orden en
     Shopify, bodega asignada, sin guía y abiertas, cuya solicitud no
     exista, haya fallado o lleve más de 6 h sin respuesta. Si el RPA está
     ocupado (409) no se marca nada y se piden en el siguiente sync. Sin
     `SODIMAC_RPA_URL` el paso se salta (RPA aún no conectado). A mano: en
     el admin, *Pedidos de marketplace* → acción "Pedir la guía al RPA de
     Sodimac" con las OC elegidas, o en el panel el botón "Generar guía
     (RPA)" de cada pedido (`request_label_for_order`; sirven también para
     reintentar una en revisión manual).
  2. Por cada OC, el RPA consulta la bodega
     (`GET /api/orders/sodimac/rpa/orders/<oc>/stock-dispatch/`): responde
     `fulfillment_location_name`, el nombre de la ubicación de Shopify. El
     RPA solo conoce **Bodega Envia, Baru y Taumm** (elige con eso la
     dirección de despacho en el portal); otra bodega le da error.
  3. El RPA genera la guía y llama al webhook
     (`POST /api/orders/sodimac/rpa/callback/`) con `orden_procesada`
     (rótulo y relación en base64). El número de guía no lo envía: se lee
     de "REMESA <número>" en el rótulo (`read_label_tracking`, `pypdf`) y se
     compara con el de la relación. Se guarda en `MarketplaceLabel` (con los
     PDF: no hay de dónde volver a pedirlos) y el despacho la toma solo.
  4. `validacion_humana`: el RPA quedó esperando un CAPTCHA; se avisa por
     correo a `SODIMAC_RPA_ALERT_EMAILS` con el enlace del visor (noVNC).
     Al resolverlo, el RPA sigue solo. `proceso_finalizado`: las OC del lote
     sin respuesta pasan a `error` y se vuelven a pedir.
  - Estado por OC en `MarketplaceOrder.label_request_status`: `solicitada`,
    `recibida`, `error` (se reintenta) o `manual` (no se reintenta; el
    motivo en `label_request_note`): guía ya generada en el portal sin PDF,
    números distintos en rótulo y relación, número no encontrado, rótulo
    TCC sin relación, o guía nueva cuando la bodega ya había sido avisada.
  - Qué pasó en la **última** corrida (desde 2026-10-15), en
    `label_request_result` (`LabelRequestResult`; solo el último, sin
    historial). Lo manda el RPA en `resultado`, salvo los dos últimos, que
    pone el backend al cerrar el lote. Se limpia al pedir la guía otra vez y
    queda vacío en la carga manual. No cambia el estado: es información.

    | Código | Cuándo |
    | --- | --- |
    | `guia_generada` | Rótulo y relación descargados. |
    | `guia_un_pdf` | Solo el rótulo (guía Home Delivery). |
    | `sin_pdf` | Clic final sin descargas. |
    | `guia_ya_generada` | Botón deshabilitado en el portal. |
    | `bodega_desconocida` | El RPA no conoce la bodega respondida. |
    | `consulta_bodega_fallida` | Falló la consulta de bodega al backend. |
    | `tiempo_agotado` | `TimeoutException` en el portal. |
    | `sin_respuesta` | Lote exitoso sin `orden_procesada` de la OC. |
    | `login_fallido` | El RPA no inició sesión. |

    Un código ausente o desconocido se guarda vacío (warning en el log): un
    código nuevo del RPA hay que agregarlo también aquí.
  - **Guía Home Delivery** (rótulo Sodimac "Paquetería - Home Delivery"):
    el portal descarga **un solo PDF** y el número ("Guía: <n>") es el de
    la OC. El RPA manda como rótulo el único PDF que no sea `RELACIONTCC_*`;
    `store_label` acepta una guía sin relación **solo si** su número es
    `marketplace_order_id` (`relation_pdf` queda vacío). El aviso a la
    bodega sale sin ese adjunto y la descarga de `relacion` da 404.
  - Token: `SODIMAC_RPA_TOKEN`, el mismo valor en las variables del RPA
    `RPA_BOT_TOKEN` (lo manda como `X-Bot-Token` al consultar la bodega) y
    `RPA_CALLBACK_AUTH_TOKEN` (`Authorization: Bearer` en el webhook). En el
    RPA: `RPA_ORDERS_API_URL_TEMPLATE` =
    `<backend>/api/orders/sodimac/rpa/orders/{numero_orden}/stock-dispatch/`
    y `RPA_CALLBACK_URL` = `<backend>/api/orders/sodimac/rpa/callback/`.
  - Panel: el listado trae `channel_label` (estado, resultado, número,
    motivo, si tiene relación) y los
    PDF se descargan por `GET /api/orders/<id>/channel-label/<rotulo|relacion>/`
    (`channel_label_file`).
  - Recuadro "RPA de guías" en el panel (`rpa_panel_status`, `rpa_continue`,
    `rpa_kill` sobre `/status`, `/continue` y `/kill` del RPA): estado,
    "Ver en vivo" (visor noVNC del RPA; en Railway hoy solo se enciende al
    aparecer un CAPTCHA; en un PC la corrida se ve en la ventana de Chrome),
    "Continuar" y "Detener".
  - Carga manual ("Cargar guía" en el panel,
    `POST /api/orders/<id>/channel-label/upload/`): cuando el RPA no puede
    (guía ya generada en el portal, RPA caído). Usa `store_label`, el mismo
    guardado y validaciones que el webhook del RPA.
  - El `/trigger` del RPA no pide credenciales (riesgo conocido del RPA:
    cualquiera con su URL puede lanzarlo).
- **Convivencia con `pamo_web`** (sin migrar datos: cada sistema factura
  sus propias OC). Los dos leen la misma cola destructiva, así que nunca
  corren a la vez: al terminar `orders.sync_sodimac` se llama en el mismo
  proceso a `POST /pamo_bots/sodimac/invoices` de `pamo_web` (síncrono,
  header `X-Bot-Token`), que reinyecta sus OC, lee la cola, devuelve a la
  cola las OC que no conoce y factura las suyas en estado final. En
  `pamo_web`, crear órdenes (`create_orders`) responde 410 y su workflow de
  GitHub quedó vacío: solo corre cuando lo llama este backend.
  - `SODIMAC_CUTOVER_DATE` (obligatoria, ISO): una OC transmitida antes es
    de `pamo_web`; no se guarda y se reinyecta para que la lea `pamo_web`.
    Es el día de la **última corrida de creación de `pamo_web`**, no el del
    despliegue (el corte se hizo con `pamo_web` pausado desde el
    2026-09-25). `SODIMAC_LEGACY_OCS` agrega las OC de ese día que ya creó
    `pamo_web`.
  - Una OC con fecha no reconocida tampoco se guarda: se reinyecta y se
    reporta como fallo.
  - Si `pamo_web` informa OC nuevas que no pudo devolver a la cola
    (`not_returned`), se reportan como fallo: hay que reinyectarlas a mano.
  - **Pendiente: fin de la convivencia.** Cuando `pamo_web` no tenga OC
    sin factura desde el 2026-04-01 (o al cumplirse el plazo acordado;
    propuesta: 60 días desde el corte, lo que quede se revisa a mano): quitar
    la llamada del paso 4, el cliente `integrations/pamo_web/` y sus
    constantes (`PAMO_WEB_*`), apagar el proceso en `pamo_web` y decidir qué
    hacer con el filtro de `SODIMAC_CUTOVER_DATE` (sin `pamo_web`, una OC
    antigua reinyectada nadie la leería).
- **Diferencias con `pamo_web`**: la etiqueta en Shopify es `sodimac` (allá
  `SODIMAC`; revisar filtros o automatizaciones de Shopify que dependan de
  la mayúscula). `pamo_web` copiaba el estado en `TrakingOrders`; aquí queda
  en `marketplace_status`. El RPA de `pamo_web` (`rpa/`,
  `stock_dispatch.py`) no se migra: el usuario confirmó el 2026-10-01 que no
  se usa. El stock hacia Sodimac sigue pendiente por un problema del lado de
  Sodimac.
- La factura de las OC en estado final es de la app `invoicing`
  ([`invoicing.md`](invoicing.md)); `orders` no la importa.

## Copia local de pedidos de Shopify

`ShopifyOrder` / `ShopifyOrderLine` guardan **todos** los pedidos de Shopify
(marketplaces y tienda web) para que el listado del frontend no dependa de
Shopify en vivo. Es el mismo patrón de `customers`: webhook firmado +
reconciliación, con una sola función de guardado.

- **Guardado único**: `upsert_shopify_order(data)` recibe el pedido
  normalizado de `integrations.shopify` (`normalize_order`). Lo usan el
  webhook, la carga inicial y la reconciliación.
  - Bloquea la fila y reemplaza todas las líneas en una transacción. Si dos
    avisos crean el mismo pedido a la vez, reintenta una vez.
  - No pisa un dato más nuevo con uno viejo (`shopify_updated_at`) y no
    revive un pedido borrado.
  - Un pedido de `list_orders_page` con más de 30 líneas se vuelve a leer
    completo con `get_order`.
  - `marketplace` se calcula desde las etiquetas, sin distinguir
    mayúsculas (`marketplace_from_tags`); sin etiqueta de marketplace →
    `shopify`.
- **Bodegas según Shopify**: `ShopifyFulfillmentOrder` (y sus
  `ShopifyFulfillmentOrderLine`) guardan los *fulfillment orders* del
  pedido: desde qué bodega sale, con qué productos y la guía si ya se
  cumplió. Se reemplazan junto con las líneas en cada guardado; es una foto
  para mostrar, este backend no decide nada con ella (la bodega que
  decide este sistema es `Dispatch`, ver "Despacho a bodegas"). Shopify
  divide pedidos web con su propio enrutamiento, por eso un pedido puede
  tener varias. Para llenarlas en pedidos viejos: `backfill_shopify_orders`.
  Los movimientos de bodega y los cumplimientos llegan por los webhooks de
  fulfillment y fulfillment order (abajo). **Sin verificar**: si mover un
  fulfillment order cambia el `updatedAt` del pedido; si no, la
  reconciliación no ve un movimiento cuyo aviso se haya perdido.
- **Borrado lógico**: `mark_shopify_order_deleted` pone `deleted_at` (o
  crea una marca borrada si no existía). El listado excluye los borrados.
- **Webhook**: `POST /api/orders/webhooks/shopify/`
  (`ShopifyOrderWebhookView`). Verifica la firma sobre el body crudo
  (`403` si falla) y lanza `orders.process_shopify_order_webhook`
  (`allow_concurrent=True`) **solo con el tema y los ids**:
  - `orders/create`, `orders/updated`, `orders/delete`: el `id` del aviso.
  - `fulfillments/create`, `fulfillments/update` (bodega cumplida, guía):
    el `order_id` del aviso.
  - `fulfillment_orders/moved`, `/split`, `/merged`, `/cancelled`,
    `/order_routing_complete` (cambios de bodega): estos avisos no traen el
    pedido; `parse_fulfillment_order_webhook` junta los ids de fulfillment
    order que mencione (gids `FulfillmentOrder` y claves
    `fulfillment_order_id`).
  - Otro tema o un aviso sin ids → `200` sin lanzar nada.
- **Proceso del webhook** (`process_shopify_order_webhook`): con ids de
  fulfillment order, busca el pedido primero en `ShopifyFulfillmentOrder`
  y, si no está, en vivo (`get_fulfillment_order_order_id`); sin pedido,
  termina sin hacer nada. Con `orders/delete` marca el borrado; si no,
  vuelve a leer el pedido con `get_order` y lo guarda (incluidos sus
  fulfillment orders) y corre `assign_web_dispatch`. Si Shopify ya no lo
  encuentra, lo marca borrado. Con `fulfillment_orders/moved`, `/split` y
  `/merged` (alguien cambió la bodega en Shopify) no asigna: alinea el
  despacho con `follow_shopify_locations` (ver "Despacho a bodegas").
- **Carga inicial** `orders.backfill_shopify_orders`
  (`allow_concurrent=False`, manual): pedidos creados desde
  `params["created_from"]` (`YYYY-MM-DD`, por defecto hoy menos 30 días).
  Si la reconciliación nunca corrió, deja su checkpoint en la hora de
  inicio.
- **Reconciliación** `orders.reconcile_shopify_orders`
  (`allow_concurrent=False`, se programa por la API del orquestador cada
  30 min): pedidos actualizados desde `ShopifyOrderSyncState.last_reconciled_at`
  menos 15 min (sin checkpoint: 30 días).
  - El checkpoint es la hora en que **empezó** la última corrida que
    terminó bien. Si una corrida falla o se cancela, no avanza.
  - No usa el máximo `shopify_updated_at`, porque los webhooks lo
    adelantan.
  - **No ve pedidos borrados**: Shopify no los lista.
- Las dos recorren las páginas con `iter_shopify_order_pages` (50 por
  página, por `UPDATED_AT` ascendente).
- **Suscripción**: desde el admin de Shopify (Configuración → Notificaciones → Webhooks),
  igual que la de `customers`, formato JSON y la URL
  `https://<host>/api/orders/webhooks/shopify/`, un webhook por evento:
  - Order creation, Order update, Order deletion.
  - Fulfillment creation, Fulfillment update.
  - Fulfillment order moved, Fulfillment orders split, Fulfillment orders
    merged, Fulfillment orders canceled, Fulfillment order's order routing
    complete.

  Los demás eventos de fulfillment order del menú (retenciones, solicitudes
  a servicios de fulfillment, retiro en tienda, entrega local, programados)
  no se suscriben: el webhook los responde con `200` sin hacer nada. No se crea por API: los
  webhooks del admin se firman con la clave que muestra esa pantalla
  (`SHOPIFY_WEBHOOK_SECRET`); los creados por API se firmarían con otro
  secreto y responderían `403`.

Verificado contra la tienda real el 2026-10-05, en una base temporal: la
carga del 1 al 5 de octubre trajo 92 pedidos con 108 líneas en 1,1 s.
Repetirla no duplicó nada.

## Listado para el frontend

Contrato: `GET /api/orders/` en
[`../contracts/API.md`](../contracts/API.md) (`OrderListAPI`,
`orders/apis.py`, roles `ORDERS_LIST_ROLES`).

- **Solo lee la base de datos.** Nunca consulta Shopify.
- `filter_orders` (`orders/functions/list_orders.py`): `ShopifyOrder` sin
  borrados, más reciente primero.
  - `marketplace`: por el campo.
  - Fechas: días de Colombia sobre `shopify_created_at`.
  - `search`: `name` sin `#`, o un `marketplace_order_number` de
    `MarketplaceOrder`.
  - Paginación: `OrderPagination` (`page`, `page_size` hasta 50).
- `format_orders` completa la página con `MarketplaceOrder` (unido por
  `shopify_order_id`, indexado, una consulta por página).
  - **Cliente**: el comprador real de la fila local; sin fila, el cliente
    de Shopify (cédula de `defaultAddress.company`, email y teléfono del
    pedido).
  - **Marketplace**: el de la fila local; si no hay, el guardado desde las
    etiquetas.
  - Un pack de Mercado Libre es un solo pedido con varios
    `marketplace_order_numbers`.
  - **Bodega** (`fulfillment`): `fulfillment_status`,
    `fulfillment_location_id`, `fulfillment_location_name` y
    `fulfillment_note` de la fila local, tal cual. Con `novedad` la bodega
    va vacía y `note` trae el motivo. Sin fila local (tienda web), todo
    vacío: este sistema no evaluó la bodega.
  - **Bodegas según Shopify** (`fulfillment_orders`): las filas de
    `ShopifyFulfillmentOrder` tal cual (prefetch, sin consultas por
    pedido).
- `list_orders_not_created` (`orders/functions/list_orders_not_created.py`):
  solo base de datos. Regla en `NOT_CREATED`: sin `shopify_order_id` y en
  `error_creando_orden`, `error_creando_cliente`, `procesando`, o
  `pending` con `error_description`. El error sale tal cual. Hasta 200,
  con el conteo real.
- `last_synced_at`: el checkpoint de la reconciliación, para mostrar qué
  tan fresca está la copia.
- `order_listing_format.py`: formato común (comprador, bodega, importes con
  `Decimal`, rango de días).

## Despacho a bodegas

Plan en curso (despachos por bodega en pedidos repartidos):
[`../implementations-plans/dispatch-per-fulfillment-order.md`](../implementations-plans/dispatch-per-fulfillment-order.md).
Este backend decide qué bodega despacha cada pedido de Shopify (marketplace
o tienda web) y le avisa por su canal; a Envía, por API.

**Conexión de la tienda con Envía (operaciones, 2026-10-11):** la tienda de
Shopify **sigue conectada** a Envía (cambia la decisión del 2026-10-07 de
desconectarla). Envía importa por esa conexión solo las órdenes **con
despacho**, así que las de marketplace se crean sin despacho (ver "Reglas de
cambio") y pasan por este flujo: bodega según el stock en Shopify y aviso a
esa bodega. Los pedidos de la tienda web siguen con despacho y Envía los
importa como hasta ahora; el aviso por API a Envía busca primero la orden
(`find_order`) y vincula la existente en vez de duplicarla. Las conexiones
directas de Mercado Libre y Falabella con Envía **se desconectan**
(decisión del 2026-10-11): sus órdenes en Envía las crea este flujo, con la
guía del canal.

- **`DispatchLocation`**: una fila por ubicación de Shopify, con canales de
  aviso independientes (`notify_api`, `notify_email`, `notify_whatsapp`),
  contactos (`emails`, `whatsapp_numbers`), `envia_warehouse_id` y
  `requires_label`. Las filas las crea y actualiza
  `sync_dispatch_locations` (proceso `orders.sync_dispatch_locations`;
  nunca toca canales ni contactos; una bodega quitada de Shopify queda
  inactiva). Se configuran en el admin de Django. `creates_own_label`
  ("La bodega crea su guía"): sus pedidos de la **tienda web** (y Addi,
  cotizaciones) se avisan sin guía y no se generan guías en Envía para
  ellos; los de marketplace siguen con su guía (del canal, del RPA o
  generada, ver el paso 2). Operaciones (2026-10-08 y 2026-10-11): así
  trabaja Bodega Envia, probablemente también Boccherini; las demás bodegas
  necesitan que se les genere la guía. La primera sincronización
  se lanza por la API del orquestador
  (`POST /api/orchestrator/process-types/orders.sync_dispatch_locations/launch/`):
  la acción del admin exige seleccionar filas y la tabla empieza vacía.
- **`Dispatch`** y **`DispatchNotification`** (uno por despacho y canal).
  Un `Dispatch` por `ShopifyOrder`, salvo los pedidos repartidos (abajo);
  nunca dos despachos del mismo pedido con la misma bodega, ni dos sin
  bodega (restricciones del modelo).
- **Pedido repartido por Shopify** (desde 2026-10-16; decisión del
  usuario: solo la **tienda web**, marketplace por ahora no): si el pedido
  tiene fulfillment orders abiertos en más de una bodega
  (`split_location_ids`, sobre la copia local), `ensure_dispatches` crea un
  despacho por bodega con `split=True`, sin recalcular inventario: se sigue
  lo que Shopify reservó. Cada uno lleva solo los productos de su
  fulfillment order (`dispatch_lines`; precio de la línea del pedido con el
  mismo SKU), que usan la guía (contenido y valor declarado) y el aviso.
  - Una bodega de Shopify sin registrar deja un despacho `manual` sin
    bodega para esa parte, con el motivo.
- **Bodega cambiada en Shopify** (desde 2026-10-16; decisión del usuario:
  nunca avisar a una bodega distinta de la que tiene Shopify). Con los
  avisos `fulfillment_orders/moved`, `/split` y `/merged`,
  `follow_shopify_locations` compara las bodegas de los fulfillment orders
  abiertos con las de los despachos:
  - iguales, o pedido ya cumplido: nada (el movimiento que hace
    `fulfill_in_shopify` después de avisar cae aquí);
  - ningún despacho con guía ni avisos: se borran y se crean en la(s)
    bodega(s) de Shopify (pedido web en varias → despachos `split`;
    marketplace en varias → uno `manual`, decisión E);
  - alguno con guía o avisos: no se reasigna (la guía salió de la bodega
    anterior); los no avisados pasan a `manual` con el motivo y los
    avisados conservan su estado con la nota.

  La asignación inicial no cambia: el enrutamiento de Shopify
  (`order_routing_complete`, `orders/*`) no reemplaza la bodega elegida por
  inventario; esa diferencia se resuelve al cumplir, cuando
  `fulfill_in_shopify` mueve el pedido a la bodega del despacho.
  - Un despacho único sin actividad (sin avisos ni guía) se reemplaza por
    los de cada bodega si Shopify ya repartió el pedido (caso #20577, que
    quedó `manual` con "ninguna bodega tiene el pedido completo").
  - **Fase 1**: se genera y descarga la guía por bodega. **Todavía no** se
    avisa por bodega ni se cumple en Shopify: el proceso salta esos
    despachos y "Notificar a proveedor" responde `400`. Eso es la fase 2
    del plan.
- **`dispatch_orders`** (proceso `orders.dispatch_orders`): pedidos creados
  desde `since`, sin despachar ni borrar ni cumplidos (`FULFILLED`). **No
  tiene interruptores** (se quitaron el 2026-10-11): se enciende creando su
  programación en el orquestador con `params={"since": "AAAA-MM-DD"}` (cada
  pocos minutos, `allow_concurrent=False`) y se pausa desactivándola, sin
  redesplegar. Sin `since` la ejecución falla en vez de tomar históricos;
  `shopify_order_ids` acota una corrida a esos pedidos (piloto) y `limit`
  limita cuántos.
  1. Bodega con `assign_dispatch_location`: en marketplace, la que eligió la
     importación (`MarketplaceOrder.fulfillment_location_*`, no se
     recalcula); en la tienda web, `select_fulfillment_location` con el
     inventario de Shopify en vivo. Sin bodega (novedad, pedido sin
     registro local, bodega sin registrar) o sin canales → `manual` con el
     motivo en `note`. Desde el 2026-10-11 todos los canales entran, también
     Madecentro y Sodimac.
  2. Guía, según el canal (si la bodega la exige, `requires_label`):

     | Canal | Guía |
     | --- | --- |
     | Mercado Libre, Falabella | Del canal por API (`fetch_dispatch_label`): `get_shipment_label` (solo en estados imprimibles) o `get_package_items` + `get_shipping_document`. En Mercado Libre, descargar una guía sin imprimir la deja impresa. |
     | Sodimac | La que carga el RPA (`MarketplaceLabel`): rótulo + relación. |
     | Madecentro | Generada en Envía con el botón "Generar guía", siempre. |
     | Tienda web (Addi, cotizaciones) | Bodega con `creates_own_label` (Bodega Envia): ninguna, se avisa sin guía y la bodega la crea. Otras bodegas: generada con el botón "Generar guía". |

     Sin guía todavía → `esperando_guia` con el motivo, y se reintenta en la
     próxima corrida. De las guías de canal se guarda el número y el hash
     del PDF; las generadas en Envía se guardan en el despacho (`label_pdf`,
     como las del RPA de Sodimac; las anteriores al 2026-10-15 se bajan de su
     URL la primera vez y quedan guardadas). La
     generación es manual por ahora (cobra); automatizarla queda pendiente
     de definir reglas (transportadora, peso y medidas).
  3. Un aviso por canal activo (`notify_dispatch.notify`). Cada aviso se
     reclama (`procesando`) antes de enviarse: nunca sale dos veces; uno en
     `error` se reintenta en la próxima corrida; uno que se quedó en
     `procesando` no se reintenta (revisión manual). Todos enviados → `notificado`;
     alguno en error → `error`.
  4. **Guía en Shopify** (`orders/functions/shopify_fulfillment.py`,
     desde 2026-10-15): apenas el despacho queda `notificado`,
     `fulfill_in_shopify` cumple el pedido en Shopify con el número y la
     transportadora de la guía, como se hacía a mano (pedido #20543). Va
     **después** del aviso porque este proceso salta los pedidos ya
     cumplidos en Shopify: cumplir antes dejaría a la bodega sin aviso.
     - Lee los fulfillment orders en vivo. Con uno abierto en otra bodega,
       primero lo mueve a la del despacho (`fulfillmentOrderMove`); luego lo
       cumple (`fulfillmentCreate`) con `notifyCustomer: false` (a mano,
       Shopify le mandó el correo de envío al cliente fijo de Sodimac).
     - Ya cumplido con la misma guía (a mano) → se marca hecho. Cumplido con
       otra guía, repartido en varias bodegas o rechazado por Shopify → el
       motivo queda en `shopify_fulfillment_error` y el despacho sigue
       `notificado`; se reintenta con "Notificar a proveedor". Sin guía en el
       despacho (bodega sin `requires_label`, tienda web con
       `creates_own_label`) no se toca Shopify.
     - Transportadora: Sodimac `TCC` (Home Delivery: `Sodimac Home
       Delivery`), Mercado Libre `Mercado Envíos`, Falabella `Falabella`,
       generada en Envía la de `label_carrier`.
     - Hecho → `shopify_fulfilled_at`. Hoy el despacho se maneja desde el
       panel; si se programa el proceso, un despacho con error aquí no se
       reintenta solo (el proceso no vuelve a tomar los `notificado`).
  5. Pedido cancelado → `cancelado` (salvo si ya se notificó).
- **Canales** (`orders/functions/notify_dispatch.py`):
  - `email`: `django.core.mail` (API de Gmail o SMTP, ver
    `architecture/INTEGRATIONS.md`), con la guía adjunta y, en Sodimac, la
    relación. Desde 2026-10-16 el cuerpo es HTML
    (`orders/templates/orders/email/dispatch_list.html`) con la misma tabla
    que el PDF de WhatsApp (imágenes enlazadas al CDN de Shopify con
    `product_image_urls`, link firmado a la guía si hay
    `BACKEND_PUBLIC_URL`; si no, solo el número); el texto plano de siempre
    va como alternativa. Sin configurar → error
    del aviso, no se intenta enviar.
  - `whatsapp`: una sola plantilla por ahora, clave `despacho_bodega`
    (aprobada en Meta como `despacho_pedido`; nombre e idioma en
    `orders/whatsapp_templates.py`, en código, no en el entorno;
    cuerpo: {{1}} bodega, {{2}} cuántos pedidos), enviada por Zernio
    (`integrations/zernio/`). En el encabezado va el **listado en PDF**
    (`dispatch_list_pdf`, `fpdf2`, A4 horizontal): una fila por producto
    con imagen, SKU, producto y cantidad; pedido (con canal y número en el
    canal), destinatario (nombre, dirección, ciudad, teléfono) y la guía
    como **link firmado** (`label_links`, vence a los 10 días) van
    combinados por pedido, para que con varios pedidos cada destinatario y
    guía quede junto a sus productos. La guía ya no va adjunta. Hoy el
    listado lleva un pedido; el aviso masivo usará la misma plantilla. Las
    imágenes salen de Shopify (`product_images`: imagen de la variante o
    del producto, bajada del CDN a 240 px); si fallan, esa fila va sin
    imagen y el aviso sigue. El PDF se sube una vez a Zernio (URL pública
    temporal, 7 días) y lo usan todos los números. Requiere
    `BACKEND_PUBLIC_URL` si hay guía. Envía a todos los números antes de
    fallar y el error dice a quiénes llegó.
  - `api`: `integrations.envia_fulfillment.create_order` con el rótulo como
    guía (pre-tracking). La relación de Sodimac va después como comentario
    con archivo (`add_order_comment`); si esa carga falla, el aviso queda
    enviado con la advertencia en `error_description` para cargarla a mano
    (un reintento vincularía la orden sin volver a cargarla). Escrituras
    reales (sin interruptor).
- **Bodega de los pedidos web** (tienda, Addi, cotizaciones; sin
  `MarketplaceOrder`): `assign_web_dispatch` crea su `Dispatch` con la bodega
  (misma regla de inventario) **apenas el pedido está pagado**, desde el
  webhook de pedidos (al instante) y la reconciliación (red de seguridad; el
  backfill no, para no asignar históricos). Una sola vez, sin avisar a nadie
  ni pedir guía; no corre mientras el registro de bodegas esté vacío. Los de
  marketplace no pasan por aquí (ya traen bodega, y el webhook puede llegar
  antes del vínculo con `MarketplaceOrder`). El listado muestra esa bodega
  en `fulfillment` (o la novedad con su motivo). Para pedidos ya existentes:
  `python manage.py assign_web_dispatches --since AAAA-MM-DD` (sin `--apply`
  solo lista).
- **Acciones manuales del panel** (`orders/functions/dispatch_actions.py`,
  endpoints `POST /api/orders/<id>/dispatch/...` en el contrato), para
  probar el flujo de forma controlada mientras no se automatiza:
  - "Notificar a proveedor": `process_order_dispatch(order)`, lo mismo que
    hace el proceso, para un pedido y sin esperar a la próxima corrida.
  - Los botones reabren un despacho en `manual` (`reopen_manual_dispatch`):
    recalculan la bodega si falta y siguen; si el motivo sigue vigente,
    vuelve a `manual` con el motivo actual. El proceso automático no los
    reabre.
  - "Traer guía" (Mercado Libre, Falabella): guía del canal. Sodimac no
    lleva botón: el aviso toma la guía cargada por el RPA.
  - Cada botón actúa sobre **un** despacho (`dispatch_for_action`): con
    varios, el panel manda `dispatch_id`.
  - "Generar guía" (tienda web, Addi, cotizaciones y Madecentro): cotiza y
    genera en Envía Shipping desde la dirección de la bodega (copiada de
    Shopify al sincronizar bodegas) a la dirección de entrega (tienda web:
    la de envío del pedido en Shopify; Madecentro: la del comprador en
    `MarketplaceOrder`, porque en Shopify va al cliente fijo). Peso (kg) y
    medidas (cm) del paquete se editan en el panel (`parse_package`: cada
    valor mayor que 0, sin tope; por defecto 1 kg 10×10×10); el panel manda
    en la generación el mismo paquete que cotizó, y el despacho guarda en
    `label_package` el paquete con que se generó (lo que se cobró). Deshabilitado en la tienda web cuando la bodega
    crea su guía. La guía generada queda en
    el despacho (`label_source="envia"`, `label_url` y el PDF en
    `label_pdf`) y no se repite; se descarga con "Descargar guía"
    (`GET .../dispatch/label/file/`). Generarla no avisa a la bodega: eso lo
    hace "Notificar a proveedor" o el proceso.
  - **Guía guardada y link firmado** (desde 2026-10-16): la guía del canal
    (Mercado Libre, Falabella, Sodimac) también queda en `label_pdf` al
    traerla para avisar; antes solo se guardaba su hash y se volvía a pedir
    al canal. Los despachos avisados antes no la tienen. De `label_pdf`
    sale `label_links.label_link(dispatch)`: un link público a
    `GET /api/orders/labels/<token>/` para quien no tiene sesión (la
    bodega). Va firmado con la `SECRET_KEY` (`django.core.signing`): abre
    solo esa guía, vence a los 10 días (`LINK_MAX_AGE`: lo que tarda un
    despacho con un puente festivo) y deja de servir si la guía cambia. No
    se guarda nada en la base: cambiar la `SECRET_KEY` invalida todos los
    links. Requiere `BACKEND_PUBLIC_URL`. Lo usa el listado en PDF del aviso
    por WhatsApp (un link por guía).
  - Aviso por **API de Envía**: busca primero la orden en Envía (nuestro
    identificador o número de Shopify, recorriendo el listado por fecha con
    `find_order`) y la vincula si existe (le carga la guía si no tiene); si
    no, la crea con `find_variant_ids` (SKU → `variantId` por el inventario
    de Envía) y el código de departamento (`resolve_colombia_state`).
  - No hay interruptores de escritura en Envía (se quitaron el
    2026-10-11). Protecciones: las pruebas bloquean la red
    (`NoNetworkTestRunner`); una guía solo se genera con el botón y nunca una
    segunda; en Envía no se duplican órdenes (aviso reclamado + búsqueda
    previa). Ojo: el `.env` local apunta a producción, así que correr el
    proceso o los botones desde un equipo escribe de verdad.
- Listado: `GET /api/orders/` trae `dispatch` por pedido (ver el contrato).
- Probar avisos sin CLI: en `/admin/` → *Bodegas de despacho* → botón
  **Probar avisos** (solo superusuarios; `orders/functions/send_test_notification.py`).
  Envía desde el servidor un correo de prueba o la plantilla
  `hello_world` por WhatsApp. Es la vía para probar en Railway: en algunos
  equipos Windows el Control inteligente de aplicaciones bloquea el
  `railway.exe` de la CLI.
- Correo: `python manage.py send_test_email <correo>` prueba el
  transporte configurado (API de Gmail o SMTP; ver
  `architecture/INTEGRATIONS.md`). En Railway solo funciona la API de
  Gmail: el SMTP está bloqueado.
- WhatsApp: `python manage.py send_test_whatsapp --list` lista las
  plantillas de la cuenta (solo lectura);
  `python manage.py send_test_whatsapp 573001234567` envía `hello_world`
  (`--template`, `--language`, `--param` para otra plantilla). Sale por
  Zernio: requiere `ZERNIO_API_KEY` y `ZERNIO_WHATSAPP_ACCOUNT_ID`.
- Pruebas: `orders/tests_dispatch.py`.

## Bodega de despacho

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
| `procesando` | Todos los canales (`claim_orders`): reclamado por un proceso que está creando la orden en Shopify. Si se queda así, el proceso murió a mitad y no se sabe si la orden quedó creada: **revisar a mano** en Shopify y corregir la fila; nada lo reintenta solo. |
| `error_creando_cliente` | **Obsoleto** — ningún código lo asigna desde el cambio a cliente fijo. Se conserva por filas históricas (ninguna de Falabella al 2026-10-05). No se reintenta solo: no está en `RETRYABLE_STATUSES`. |
| `error_creando_orden` | Falló la creación de la orden (SKU sin resolver, o Shopify rechazó la orden). `error_description` tiene el detalle. |
| `orden_creada` | Éxito — `shopify_order_id`/`shopify_order_name` quedan guardados junto con `marketplace_order_number`. |

## Reglas de cambio

- No se hace el intento de crear la orden si algún ítem no resuelve un
  `shopify_variant_id` — se marca error completo, no una orden parcial.
- `financial_status="PAID"` es el valor por defecto de `process_shipment`:
  los pedidos de marketplace llegan ya pagados por el comprador, Shopify
  solo registra la venta. Excepción: Sodimac (`PENDING`, paga a crédito).
- **Despacho en Shopify** (`requiresShipping` en cada línea,
  `process_shipment.REQUIRES_SHIPPING` → `create_order`): **todas** las
  órdenes de marketplace van **sin despacho** ("No se requiere envío").
  La tienda de Shopify sigue conectada a Envía y Envía solo importa las
  órdenes con despacho: así no toma por su cuenta los pedidos de
  marketplace, cuya bodega decide este backend (ver "Despacho a bodegas").
  Mercado Libre desde el 2026-10-05; Falabella, Madecentro y Sodimac desde
  el 2026-10-11 (decisión con operaciones). El campo se envía siempre
  explícito: sin él Shopify mostraba "No se requiere envío" en todo
  (corregido 2026-10-02). Las órdenes creadas antes no se modifican.
- El número de orden del marketplace va en el `note` de la orden de
  Shopify (`"{marketplace} #{numero}"`) — no se usa un campo dedicado (no
  se verificó si existe uno).
- **Idempotencia al crear en Shopify** (`orderCreate` no tiene clave de
  idempotencia propia). Dos capas, en todos los canales:
  1. **Misma base**: `claim_orders` deja el pedido en `procesando` antes de
     llamar a Shopify. Si `create_order()` falla por red, no se sabe si la
     orden quedó creada: el pedido se queda en `procesando` para revisión
     manual y no se reintenta a ciegas. (Antes, Falabella lo reintentaba en
     la corrida siguiente.)
  2. **Otra base u otro proceso con la misma tienda**: cada orden lleva la
     etiqueta `<marketplace>-<número>` (ej. `falabella-3254084998`; en un
     pack, una por número). Antes de crear, `process_shipment` busca en
     Shopify `tag:"<etiqueta>"` (`list_orders_page`); si ya existe una
     orden, la vincula (`orden_creada` con ese id) y no crea otra. La
     búsqueda de Shopify se indexa con unos segundos de retraso, así que no
     reemplaza la capa 1. Las órdenes creadas antes del 2026-10-05 no
     llevan esa etiqueta.

  Origen: el 2026-10-05 el pedido de Falabella 3254084998 se creó 8 veces
  (#20352–#20359, 10:36–10:37 UTC) con nuestro token y nuestra nota, pero
  sin fila en la base de Railway ni ejecución del orquestador: un proceso
  local con otra base contra la tienda real. La orden buena es #20360. Las
  pruebas ya no pueden llamar a proveedores (ver "Pruebas").

## Pruebas

`orders/tests.py`: cada etapa probada por separado con las dependencias
externas mockeadas. `manage.py test` corre con
`config.test_runner.NoNetworkTestRunner`, que hace fallar cualquier llamada
HTTP real (las pruebas leen las credenciales reales de `.env`). Las clases
que pasan por `process_shipment` heredan `NoExistingShopifyOrderMixin`
(la búsqueda de la orden existente devuelve vacío; `self.mock_find_existing`
la cambia). Cubre: descubrimiento de pedidos nuevos sin
duplicar (con los datos de facturación del comprador), orden creada con el
cliente fijo y el `note` del comprador, fallo temprano sin cliente fijo
configurado, reintento solo de `pending` / `error_creando_orden`
(`procesando` y `error_creando_cliente` no se tocan), fallo de red al
crear deja `procesando`, orden ya existente en Shopify vinculada sin crear
otra, etiqueta por pedido (también en packs), bodega
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

Sodimac (mocks de `integrations.sodimac`, `integrations.pamo_web` y
Shopify): OC nueva guardada con ítems y creada `PENDING` con la nota; OC
guardada aunque Shopify la rechace; OC conocida solo actualiza estado; la
misma OC en los dos tipos se guarda una vez; reinyección solo de abiertas y
un fallo de reinyección no detiene el resto; OC anterior al corte o en
`SODIMAC_LEGACY_OCS` vuelve a la cola sin guardarse; fecha con día primero;
fecha no reconocida vuelve a la cola y marca error; fallo al leer un tipo
no impide leer el otro; `limit`; reintento de OC en error sin tocar
`procesando`; fallo de `pamo_web` y OC no devueltas por `pamo_web` se
reportan; falla temprano sin cliente fijo o sin fecha de corte;
`ProcessType` sembrado. Kits (en las pruebas de Madecentro): una línea por
componente con precio repartido, componente inexistente en Shopify y kit
vacío.

Copia local de Shopify (Shopify simulado):
- `upsert_shopify_order`: crea con líneas y marketplace desde etiquetas,
  reemplaza líneas, no pisa con un dato más viejo, no revive un borrado,
  relee un pedido truncado (y lo marca borrado si ya no existe), reintenta
  ante `IntegrityError`.
- Proceso del webhook: crea y actualiza releyendo; `orders/delete` marca
  sin leer; pedido inexistente cuenta como borrado.
- Webhook: firma válida lanza el proceso solo con tema e id; firma inválida
  o ausente → `403`; tema desconocido o sin id → `200` sin lanzar.
- Carga y reconciliación: todas las páginas por `UPDATED_AT`, ventana por
  defecto, checkpoint con solape, checkpoint que no avanza si se cancela o
  falla, `ProcessType` sembrados.

Listado (con Shopify bloqueado: falla si se llama): ambas listas desde la
base, comprador real, números y bodega desde `MarketplaceOrder`, pedido
web, pack de Mercado Libre, cada filtro, excluye borrados, orden,
paginación por página con `last_synced_at`, consultas que no crecen con
los pedidos, `403` sin sesión o rol, `400` por parámetro y `404` por
página inexistente. Pedidos no creados: estados incluidos y excluidos,
error, ítems y total, filtros, tope con conteo, sin N+1.

La prueba `test_process_registered_in_orchestrator_registry`
falla hoy: `orchestrator/registrations.py` solo se carga con
`RUN_MAIN=true` o `ORCHESTRATOR_FORCE_READY` (ver
[`orchestrator.md`](orchestrator.md)), y la corrida de pruebas no define
ninguno de los dos.
