# Plan: pedidos de Mercado Libre a Shopify por webhook

Estado: **fases 0 y 1 implementadas (2026-09-24)**. Fase 0: cuenta
conectada, levantamiento de datos hecho, funciones de lectura verificadas.
Fase 1: webhook, procesamiento por envío y recuperación, con pruebas sin
red en verde (`python manage.py test orders integrations.mercadolibre`,
salvo la falla conocida del registro en memoria). Decisiones C (cliente
fijo), E (envío = una bodega = una orden de Shopify) y G (recuperación)
confirmadas. **En producción desde el 2026-09-24**: webhook configurado en
Mercado Libre Developers y recibiendo notificaciones reales
(`orders.process_mercadolibre_notification`); orquestador verificado en
Railway (programación de Falabella disparada por el scheduler). Pendiente:
programar `orders.recover_mercadolibre`, dejar la hora definitiva de
`orders.import_falabella`, y vigilar filas en `procesando` (revisión
manual). Lecciones del despliegue: aplicar migraciones solo desplegando
el código que las acompaña (el `.env` local apunta a la base de Railway;
un `migrate` local dejó columnas obligatorias que el código desplegado no
conocía), y cambiar horarios por la API del orquestador, no en la base ni
en `/admin/` (el scheduler solo relee la base al arrancar).
Reutiliza el modelo
`MarketplaceOrder`, la creación de la orden en Shopify y la selección de
bodega de la app `orders` (ver
[`marketplace-orders-import.md`](marketplace-orders-import.md) y
[`shopify-inventory-by-location.md`](shopify-inventory-by-location.md)),
pero **cambia el disparo**: no se escanea Mercado Libre buscando pedidos
nuevos; cada notificación de Mercado Libre dispara el procesamiento de ese
pedido.

## Objetivo y fases

- **Fase 0 — conexión y levantamiento de datos (solo lectura)**. Conectar
  la cuenta, leer pedidos reales y documentar qué campos trae el pedido.
  Con eso se decide si se crean clientes reales en Shopify o se usa un
  cliente fijo (decisión C).
- **Fase 1 — webhook**. Cada pedido pagado que notifica Mercado Libre se
  crea en Shopify.

Fuera de ambas fases: etiquetas/guías de Mercado Envíos, mensajería,
reclamos, devoluciones, pedidos Full y sincronización de inventario hacia
Mercado Libre. (Los packs sí entran: decisión E.)

`pamo-one-engineering/apps/api/orders/mercadolibre_*.py` es solo
referencia de endpoints (`/oauth/token`, `/orders/{id}`, `/shipments/{id}`,
`/missed_feeds`, receptor de notificaciones). **No se copia**, y su
normalizador excluía a propósito los datos del comprador
(`sensitiveFieldsExcluded`), así que no responde la pregunta de clientes.

## Cómo notifica Mercado Libre (a verificar en fase 0/1)

- La app de Mercado Libre Developers se configura con **una URL de
  notificaciones** y los tópicos suscritos. Para pedidos: `orders_v2`.
- La notificación **no trae el pedido**, solo un puntero:
  `{"_id", "resource": "/orders/<id>", "user_id", "topic",
  "application_id", "attempts", "sent", "received"}`. Siempre hay que
  consultar `GET /orders/<id>` con el token propio.
- Llega **una notificación por cada cambio** del pedido (creado, pagado,
  actualizado…), no una sola por pedido. El proceso debe ser idempotente.
- Espera un `200` rápido (la documentación menciona ~500 ms); si no, lo
  reintenta. Las que no se entregaron se pueden consultar en
  `GET /missed_feeds?app_id=<id>` durante un tiempo limitado.
- **No trae firma HMAC**. Ver decisión B.

## Fase 0 — conexión y levantamiento de datos

### Cambios

1. **`config/constants.py` y `.env.example`**:
   `MERCADOLIBRE_CLIENT_ID`, `MERCADOLIBRE_CLIENT_SECRET`,
   `MERCADOLIBRE_REDIRECT_URI`, `MERCADOLIBRE_API_BASE_URL`
   (default `https://api.mercadolibre.com`), todos con `default=""` para
   que el backend arranque sin ellos.
2. **`integrations/mercadolibre/models.py`** — `MercadoLibreToken`
   (fila única `id=1`, estado de conexión, mismo criterio que
   `SiigoToken`): `access_token`, `refresh_token`, `expires_at`, `user_id`
   (seller id que devuelve `/oauth/token`), `updated_at`. Reexportar en
   `integrations/models.py`; `makemigrations integrations`.
3. **`integrations/mercadolibre/client.py`** — `MercadoLibreClient`:
   - `exchange_code(code)`: `grant_type=authorization_code`.
   - `get(path, params=None)`: Bearer con token vigente; renueva si faltan
     menos de 5 min y reintenta **una vez** ante 401.
   - El refresh token de Mercado Libre es **de un solo uso y rota** en
     cada renovación. La renovación va dentro de `transaction.atomic()` con
     `select_for_update()` sobre la fila, releyendo `expires_at` tras el
     bloqueo, y guarda el `refresh_token` nuevo antes de devolver el
     access token. Esto importa más con webhooks: varios procesos
     concurrentes pueden intentar renovar a la vez.
   - `MercadoLibreAPIError(status_code, body)` como `SiigoAPIError`;
     `invalid_grant` o sin fila de token → mensaje que indica re-autorizar.
4. **Conexión de la cuenta desde el navegador** (cambio decidido el
   2026-09-24: el backend tiene URL pública y la app de Mercado Libre es
   nueva y exclusiva de este backend, así que reemplaza al comando de
   gestión que proponía la primera versión del plan).
   `integrations/mercadolibre/apis.py` + `integrations/urls.py`, montado
   en `/api/integrations/`:
   - `GET mercadolibre/connect/` (rol `Admin`): genera un `state` de un
     solo uso, lo guarda en la sesión y redirige (302) a la autorización.
   - `GET mercadolibre/callback/` (rol `Admin`, la misma sesión vuelve en
     la redirección): consume el `state` siempre, lo valida (tiempo
     constante, máximo 10 min), maneja `error` (vendedor rechazó) y llama
     `exchange_code`. Responde `{"connected": true, "seller_id"}`; nunca
     devuelve tokens.
   Una variable con refresh token no sirve porque éste rota en el primer
   uso. Sin PKCE: `state` atado a la sesión basta para una app
   confidencial con `client_secret`; si la app de Mercado Libre se
   configura exigiendo PKCE, hay que agregarlo.
5. **Funciones de lectura** en `integrations/mercadolibre/functions/`,
   que se reutilizan tal cual en fase 1:
   - `get_order(order_id)` → pedido normalizado:
     `{"order_id", "order_number", "pack_id", "shipment_id", "status",
     "created_at", "buyer": {...}, "items": [{"sku", "quantity",
     "price"}]}`.
   - `get_billing_info(order_id)` → identificación, tipo de documento,
     nombre, apellido, dirección, ciudad, departamento.
   - `get_shipment(shipment_id)` → `logistic_type`, estado y dirección del
     destinatario.
   Mientras dure el levantamiento, cada función conserva también el JSON
   crudo en la clave `raw` para poder inspeccionarlo; se retira al cerrar
   la fase.
6. **`integrations/mercadolibre/tests.py`** — sin red: renovación que
   guarda el refresh token nuevo, reintento único ante 401,
   `invalid_grant`, sin fila de token, normalización con campos ausentes.

### Implementado (2026-09-23)

- `config/constants.py` / `.env.example`: además de las variables del
  plan, `MERCADOLIBRE_AUTH_URL` (default
  `https://auth.mercadolibre.com.co/authorization`).
- `integrations/mercadolibre/models.py` + migración
  `integrations/0002_mercadolibretoken.py`.
- `integrations/mercadolibre/client.py`: `MercadoLibreClient`
  (`authorization_url`, `exchange_code`, `get`), `MercadoLibreAPIError`,
  `MercadoLibreAuthError`.
- `integrations/mercadolibre/functions/get_order.py`,
  `get_billing_info.py` (endpoint nuevo `/orders/billing-info/MCO/{id}`),
  `get_shipment.py` (con `items` de `shipping_items`) y `get_pack.py`. El
  `raw` del levantamiento ya se retiró.
- `orders/functions/select_fulfillment_location.py`: suma cantidades del
  mismo SKU antes de evaluar (cambio 2b de la fase 1, adelantado por no
  depender de ninguna decisión).
- `integrations/mercadolibre/apis.py`, `integrations/urls.py` y la ruta
  `api/integrations/` en `config/urls.py`.
- `integrations/mercadolibre/tests.py` (cliente, flujo de conexión con
  permisos y `state`, funciones de lectura).

### Pasos para conectar la cuenta

1. En Mercado Libre Developers (Colombia) crear la app con redirect URI
   `https://<host-público>/api/integrations/mercadolibre/callback/`
   (idéntica a `MERCADOLIBRE_REDIRECT_URI`) y permisos de lectura de
   órdenes y envíos con `offline_access`.
2. Configurar `MERCADOLIBRE_CLIENT_ID`, `MERCADOLIBRE_CLIENT_SECRET` y
   `MERCADOLIBRE_REDIRECT_URI` en ese entorno.
3. `python manage.py migrate integrations` en ese entorno.
4. Con sesión iniciada como `Admin` en el backend, abrir
   `https://<host-público>/api/integrations/mercadolibre/connect/` en el
   navegador, entrar con la cuenta del vendedor y aceptar. Debe terminar
   en `{"connected": true, "seller_id": "..."}`.

### Levantamiento de datos (entregable de la fase 0)

Con la cuenta real, sobre al menos 5 pedidos pagados recientes (incluir
uno con variación, uno de pack y uno Full si existen), llamar
`get_order`, `get_billing_info` y `get_shipment` desde `manage.py shell`
y registrar **en este documento** una tabla con: campo, endpoint de
origen, si viene siempre/a veces/nunca, y formato. **Sin valores reales**
de compradores (nombres, documentos, teléfonos, correos, direcciones).

Los ids de pedido se sacan del panel de ventas o, solo para este
levantamiento, con una búsqueda puntual desde el shell (no es una
función del proveedor; la fase 1 no escanea):

```python
from integrations.mercadolibre.client import MercadoLibreClient
from integrations.mercadolibre.models import MercadoLibreToken
from integrations.mercadolibre.functions.get_order import get_order
from integrations.mercadolibre.functions.get_billing_info import get_billing_info
from integrations.mercadolibre.functions.get_shipment import get_shipment

seller = MercadoLibreToken.objects.get(id=1).user_id
page = MercadoLibreClient().get("/orders/search", {"seller": seller, "order.status": "paid", "sort": "date_desc", "limit": 5})
ids = [o["id"] for o in page["results"]]
order = get_order(ids[0]); order["raw"].keys()
get_billing_info(ids[0])["raw"]
get_shipment(order["shipment_id"])["raw"]
```

#### Resultado (2026-09-24)

Cuenta conectada (token guardado en `MercadoLibreToken`). Revisados los 8
pedidos pagados más recientes (de 1.345 pagados en la cuenta) con un
script de solo lectura que imprimió estructura y presencia, nunca valores
personales. Presencia = pedidos con el campo no vacío / pedidos revisados.

| Campo | Endpoint | Presencia | Formato / observación |
| --- | --- | --- | --- |
| `buyer.first_name` | `/orders/{id}` | 8/8 | Texto; a veces trae nombre completo. |
| `buyer.last_name` | `/orders/{id}` | 6/8 | Texto. |
| `buyer.id`, `buyer.nickname` | `/orders/{id}` | 8/8 | Id numérico y apodo de Mercado Libre. |
| `buyer.billing_info.id` | `/orders/{id}` | 8/8 | Id numérico largo; llave del endpoint nuevo de facturación. |
| Email del comprador | ninguno | 0/8 | No aparece en pedido ni en envío. |
| Teléfono | `/shipments/{id}` `receiver_address.receiver_phone` | 8/8 | **Enmascarado** (letras fijas, sin dígitos): inutilizable. |
| Documento (tipo y número) | facturación | 8/8 | `CC` (6) o `NIT` (2). Requiere permiso de facturación, ver abajo. |
| `order_items[].item.seller_sku` | `/orders/{id}` | 8/8 | SKU del vendedor; formatos numéricos y con guiones. |
| `order_items[].item.variation_id` | `/orders/{id}` | 0/8 | Vacío aunque 7/8 traen `variation_attributes`: el SKU de la variante llega en `seller_sku`. |
| `order_items[].item.seller_custom_field` | `/orders/{id}` | 0/8 | No se usa. |
| `order_items[].quantity`, `unit_price` | `/orders/{id}` | 8/8 | Número; `currency_id` = `COP`. |
| `pack_id` | `/orders/{id}` | 3/8 | Numérico; **3 de 8 pedidos son parte de un pack**. |
| `shipping.id` | `/orders/{id}` | 8/8 | Id del envío. |
| `status` | `/orders/{id}` | 8/8 | `paid` (filtro de la búsqueda); `payments[].status` = `approved`. |
| `date_created` | `/orders/{id}` | 8/8 | ISO-8601 con milisegundos y offset. |
| `logistic_type` | `/shipments/{id}` | 8/8 | `cross_docking`, `self_service` (Flex). **Ningún Full** en la muestra. Formato clásico, sin header especial. |
| `receiver_address.receiver_name` | `/shipments/{id}` | 8/8 | Nombre de quien recibe. |
| `receiver_address.street_name`, `street_number`, `address_line` | `/shipments/{id}` | 8/8 | Dirección completa. |
| `receiver_address.neighborhood.name`, `comment` | `/shipments/{id}` | 8/8 | Barrio y referencias. |
| `receiver_address.city.name`, `state.name`, `zip_code` | `/shipments/{id}` | 8/8 | Ciudad, departamento, código postal. |

**Facturación**: sin el permiso de facturación de la app, tanto
`GET /orders/{id}/billing_info` como
`GET /orders/billing-info/MCO/{billing_info_id}` (header `x-version: 2`)
responden **403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`**. Con el permiso
habilitado en Mercado Libre Developers (2026-09-24) responden los dos en
8/8, **sin volver a conectar la cuenta**. `get_billing_info` usa el nuevo
(llave `buyer.billing_info.id`, que `get_order` entrega como
`billing_info_id`) porque además trae dirección y tipo de cliente:

| Campo (endpoint nuevo) | Presencia | Observación |
| --- | --- | --- |
| `buyer.billing_info.identification.type` / `.number` | 8/8 | `CC` (6), `NIT` (2). |
| `buyer.billing_info.name` | 8/8 | En NIT, razón social. |
| `buyer.billing_info.last_name` | 6/8 | Vacío en los 2 NIT. |
| `buyer.billing_info.address.street_name`, `street_number` | 8/8 | |
| `buyer.billing_info.address.city_name`, `state.name`, `state.code` | 8/8 | `state.code` con forma `CO-XX`. |
| `buyer.billing_info.address.neighborhood`, `comment` | 7/8, 6/8 | |
| `buyer.billing_info.attributes.cust_type` | 8/8 | `CO` con todos los CC, `BU` con todos los NIT. |
| Email / teléfono | 0/8 | **No vienen tampoco en facturación.** |

**Conclusiones para las decisiones:**

- **C (clientes)**: **no se puede crear el cliente real** en Shopify. No
  hay email en ningún endpoint y el teléfono viene enmascarado;
  `create_customer` exige uno de los dos. → **C2, cliente fijo**, salvo
  que se decida crear clientes sin email ni teléfono.
- **Documento para Siigo**: disponible (tipo, número, nombre o razón
  social, dirección, ciudad, departamento y persona natural/empresa).
  Para Siigo, el tipo de persona sale de `customer_type` (a diferencia de
  Falabella, que se asume siempre persona natural).
- **SKU**: `seller_sku` resuelve también las variantes.
- **D (Full)**: no apareció en la muestra; la regla se mantiene por si
  aparece.
- **E (packs)**: en una muestra de 50 pedidos pagados, 21 tenían
  `pack_id` pero **solo 1 pack tenía más de una orden** (2 órdenes, mismo
  comprador, **mismo `shipping.id`**). Mercado Libre asigna `pack_id` a
  toda compra por carrito, aunque tenga una sola orden. Resuelto con la
  regla de negocio de la decisión E (fase 1).

Preguntas que la tabla debe responder:

1. **Email del comprador**: ¿viene? ¿es real o un alias/enmascarado de
   Mercado Libre?
2. **Teléfono**: ¿viene en el pedido, en billing info o en el envío?
   ¿completo o enmascarado?
3. **Documento**: tipo (CC, NIT, CE…) y número; ¿siempre presente?
4. **Nombre, apellido, dirección, ciudad, departamento**: ¿de billing info,
   del envío, o de ambos?
5. **Endpoint de facturación**: se espera `GET /orders/{id}/billing_info`;
   Mercado Libre anunció uno nuevo
   (`/orders/billing-info/{site_id}/{billing_info_id}`). ¿Cuál responde?
6. **SKU**: ¿`order_items[].item.seller_sku` trae el SKU de la variación?
   ¿Hay pedidos sin `seller_sku`?
7. **`logistic_type`** en `/shipments/{id}` (puede requerir el header
   `x-format-new: true`) y valores presentes en la cuenta.
8. **Número visible**: ¿el equipo identifica la venta por `pack_id` o por
   el `id` de la orden?

## Decisión C — clientes en Shopify

**Decidido (2026-09-24): C2, cliente fijo**, igual que Falabella
(`MERCADOLIBRE_SHOPIFY_CUSTOMER_ID`). Mercado Libre no entrega email ni
teléfono en ningún endpoint.

Criterio: `integrations.shopify.create_customer` exige **email o
teléfono** (validación defensiva propia, ver su docstring). Si Mercado
Libre no entrega ninguno de los dos real, no se puede crear el cliente sin
inventar un dato de contacto — misma regla que se aplicó con Falabella.

- **C1 — hay datos suficientes**: se crea/reutiliza el cliente real. No se
  construye nada nuevo; se reutiliza la app `customers`:
  `customers.functions.find_by_identification` (lookup local por
  documento) → si no existe, `integrations.shopify.create_customer` +
  `customers.functions.set_identification` (documento en `company`). El
  cliente creado entra al directorio local por el webhook de clientes de
  Shopify o por la reconciliación nocturna. Implica que `orders` vuelve a
  depender de `customers` **solo para Mercado Libre**; actualizar
  `APP_BOUNDARIES.md` y `docs/apps/customers.md`.
- **C2 — no hay datos suficientes**: cliente fijo
  `MERCADOLIBRE_SHOPIFY_CUSTOMER_ID`, igual que Falabella, con el
  comprador en el `note` y en `MarketplaceOrder`.
- **Riesgo de C1 a evaluar con los datos**: un email alias de Mercado
  Libre cambia por pedido o por comprador; crear clientes con él puede
  llenar Shopify de duplicados. Si el email es alias, C1 solo vale si hay
  teléfono real o si se acepta el documento como única llave (vía
  `find_by_identification`).

En ambos casos se guardan en `MarketplaceOrder` todos los datos de
facturación disponibles, y se agrega
`customer_identification_type = CharField(max_length=16, blank=True)`
(Mercado Libre entrega el tipo y un comprador puede ser empresa con NIT;
la futura factura en Siigo lo necesita).

## Fase 1 — webhook

### Flujo

```
Mercado Libre ──POST──▶ orders/webhooks.py (MercadoLibreOrderWebhookView)
                         valida forma + application_id + user_id
                         launch_process("orders.process_mercadolibre_notification")
                         200 inmediato
                                  │
                                  ▼
          orders/functions/process_mercadolibre_notification.py
            get_order(id) ─ status != paid → no hace nada (llegará otra notificación)
                          ─ Full → no hace nada (decisión D)
            get_billing_info / get_shipment
            upsert MarketplaceOrder + ítems (get_or_create), con shipment_id
            ¿pack? → órdenes del pack (decisión E); ¿el envío está completo? no → esperar
            reclamar TODAS las órdenes del envío (atómico)
            → process_shipment(orders, customer): una bodega, una orden de Shopify
```

### Decisiones propuestas

- **A. Solo pedidos pagados**. Una notificación de un pedido no pagado se
  ignora sin persistir; la de "pagado" llega después. La orden se marca
  `PAID` en Shopify, así que importar uno no pagado sería falso.
- **B. Autenticación sin firma**. `PROVIDER_WEBHOOKS.md` pide verificar
  firma, pero Mercado Libre no firma. Mitigación:
  1. El payload nunca se usa como dato: solo se extrae el id y el pedido
     se lee de la API con el token propio. Un payload falso no puede
     inyectar un pedido.
  2. Validar `topic == "orders_v2"`, `resource` con forma
     `^/orders/\d+$`, `application_id == MERCADOLIBRE_CLIENT_ID` y
     `user_id` == seller id guardado en `MercadoLibreToken`; si no, `200`
     sin lanzar proceso (no dar pistas ni provocar reintentos).
  3. Opcional: segmento secreto en la URL de notificaciones
     (`MERCADOLIBRE_WEBHOOK_PATH_SECRET`), comparado con
     `hmac.compare_digest`. Reduce ruido, no es autenticación fuerte.
  Documentar esta excepción en `PROVIDER_WEBHOOKS.md`.
- **C.** Ver arriba.
- **D. Pedidos Full se omiten** (`logistic_type == "fulfillment"`):
  despacha Mercado Libre desde su bodega; asignar bodega Pamo sería
  incorrecto. Qué hacer con ellos queda para después.
- **E. La unidad de despacho es el envío** (regla de negocio confirmada
  por el usuario el 2026-09-24): un envío = una guía = **una sola
  bodega**. No se parte un envío entre bodegas; si ninguna bodega puede
  despachar el envío completo, es **novedad** (y la orden en Shopify se
  crea igual, regla vigente de `shopify-inventory-by-location.md`).
  Aplicación:
  - Se sigue guardando una `MarketplaceOrder` por orden de Mercado Libre
    (el `UniqueConstraint` y el reclamo por fila no cambian), con el campo
    nuevo `shipment_id`.
  - Las órdenes que comparten `shipment_id` se procesan **juntas**: una
    sola evaluación de bodega con los ítems de todas y **una sola orden de
    Shopify** con todas las líneas; el mismo `shopify_order_id`,
    `fulfillment_*` y estado quedan en cada `MarketplaceOrder` del envío.
    (Una orden de Shopify por envío = un picking por guía; **confirmado
    por el usuario el 2026-09-24**.)
  - **Envío completo antes de procesar**: si la orden notificada tiene
    `pack_id`, se consultan las órdenes del pack con
    `GET /packs/{pack_id}` (verificado el 2026-09-24 contra el pack real
    de 2 órdenes: devuelve `orders: [{"id"}, …]`, `shipment: {"id"}`,
    `status`) y se procesa solo cuando todas están pagadas y persistidas.
    Control adicional: las cantidades por **id de publicación**
    (`order_items[].item.id`) de las órdenes deben coincidir con
    `shipping_items[].id`/`quantity` de `/shipments/{id}` (`shipping_items`
    viene por id de publicación `MCO…`, no por SKU). Si falta algo, no se
    procesa: la notificación de la orden que falta vuelve a disparar el
    proceso.
  - `pack_id` (si existe) es el `marketplace_order_number` visible; si no,
    el `id` de la orden.
  - Frecuencia observada: ~1 de cada 50 pedidos es un pack con más de una
    orden; el camino normal es un envío de una sola orden.
- **F. Concurrencia por pedido**. El `ProcessType` necesita
  `allow_concurrent=True` (regla de `PROVIDER_WEBHOOKS.md`), pero Mercado
  Libre manda varias notificaciones del mismo pedido casi a la vez
  (p. ej. pagado + actualizado). Sin control, dos procesos crearían la
  misma orden dos veces en Shopify. Propuesta:
  - `get_or_create` por `(marketplace, marketplace_order_id)` (el
    `UniqueConstraint` ya existe) para la fila;
  - estado nuevo `Status.PROCESSING = "procesando"` y reclamo atómico **de
    todas las órdenes del envío** dentro de `transaction.atomic()`:
    `select_for_update()` sobre las filas con ese `shipment_id`, verificar
    que todas siguen sin `shopify_order_id` y en `PENDING`/`ERROR_ORDER`,
    y pasarlas a `PROCESSING`. Si alguna ya no cumple, otro proceso tiene
    el envío y este termina sin hacer nada.
  - Un pedido que queda en `procesando` (proceso muerto a mitad de
    `create_order`) **no se reintenta solo**: es justo el caso de
    resultado desconocido del riesgo de duplicado abierto en
    `marketplace-orders-import.md`. Se revisa a mano en `/admin/`. Para
    Mercado Libre esto resuelve ese riesgo; para Falabella no cambia nada.
- **G. Recuperación** (confirmada por el usuario el 2026-09-24, con los
  tres casos). Sin escaneo de pedidos,
  un pedido puede quedar sin llegar a Shopify de tres formas distintas:
  1. **Aviso no entregado**: Mercado Libre no recibió `200` a tiempo
     (backend caído o desplegando, 5xx, respuesta lenta). Reintenta por su
     cuenta un tiempo y después lo deja en `GET /missed_feeds`. → Leer
     `missed_feeds` del tópico `orders_v2` y procesar cada id con la misma
     función del webhook.
  2. **Aviso entregado, proceso fallido**: el webhook respondió `200`
     pero el proceso en segundo plano falló antes de guardar (error de la
     API de Mercado Libre, token). No está en `missed_feeds` ni en
     `MarketplaceOrder`. → Relanzar las `ProcessExecution` de
     `orders.process_mercadolibre_notification` en `ERROR` de las últimas
     horas con sus mismos `params` (sin modelo nuevo; se reutiliza el
     registro del orquestador). Límite de reintentos por ejecución para
     no ciclar sobre un error permanente.
  3. **Pedido guardado, orden no creada** (`error_creando_orden`, p. ej.
     SKU inexistente en Shopify): Mercado Libre no vuelve a avisar. →
     Reintentar desde la base los `MarketplaceOrder` de Mercado Libre en
     ese estado (agrupados por envío).
  Además, reportar envíos de pack que siguen incompletos tras varias
  horas. Proceso programado `orders.recover_mercadolibre` (pocas veces al
  día, `allow_concurrent=False`).

### Cambios

1. **`orders/models.py`** + migración: `Marketplace.MERCADOLIBRE`,
   `Status.PROCESSING`, `customer_identification_type`, `customer_type`
   (`CO`/`BU` de Mercado Libre, para el tipo de persona en Siigo) y
   `shipment_id` (`CharField(max_length=32, blank=True, db_index=True)`;
   vacío en Falabella).
2. **`orders/functions/process_pending_orders.py`**: extraer
   `_process_one_order` como función pública que recibe **una lista de
   órdenes del mismo envío** (`process_shipment(orders, customer_id)`, en
   su propio archivo): resuelve SKUs de todas, elige una bodega, crea una
   orden de Shopify con todas las líneas y guarda el resultado en cada
   orden. Falabella la llama con `[order]` (un pedido = un envío), sin
   cambio de comportamiento. `process_pending_orders` filtra por
   `marketplace=FALABELLA` para no tomar pedidos de Mercado Libre.
   `_order_note` lista todos los números de orden del envío y usa
   `customer_identification_type` si viene (`"CC"` por defecto); para NIT,
   la razón social.
2b. **Hecho en la fase 0.** `orders/functions/select_fulfillment_location.py`:
   sumar cantidades por SKU antes de evaluar. Hoy compara cada línea por
   separado contra el stock; con dos órdenes del mismo envío que traen el
   mismo SKU (x1 y x1), una bodega con 1 unidad "cubriría" ambas líneas.
   Falabella no se ve afectada (`get_order_items` ya agrupa por SKU), pero
   la regla debe ser correcta para cualquier entrada.
2c. **Hecho en la fase 0.** `integrations/mercadolibre/functions/get_pack.py`
   → `{"pack_id", "order_ids", "shipment_id", "status"}` desde
   `/packs/{pack_id}`. `get_shipment` agrega `items` desde
   `shipping_items` (`item_id`, `quantity`).
3. **`orders/functions/parse_mercadolibre_notification.py`**: valida la
   forma del payload y devuelve el `order_id` o `None`.
4. **`orders/functions/upsert_mercadolibre_order.py`**: persiste
   `MarketplaceOrder` + ítems desde `get_order`/`get_billing_info`. La
   usan el webhook y la recuperación.
5. **`orders/functions/process_mercadolibre_notification.py`**: callable
   registrado; flujo del diagrama; resuelve el cliente según la decisión C.
6. **`orders/functions/recover_mercadolibre_orders.py`**: decisión G.
7. **`integrations/mercadolibre/functions/get_missed_feeds.py`**.
8. **`orders/webhooks.py`** — `MercadoLibreOrderWebhookView`
   (`AllowAny`, validación de la decisión B, `launch_process`, `200`).
   Ruta `POST /api/orders/webhooks/mercadolibre/` en `orders/urls.py`.
9. **`orchestrator/registrations.py`** + migración de siembra (patrón de
   `orders/migrations/0002_seed_process_type.py`):
   `orders.process_mercadolibre_notification` (`allow_concurrent=True`) y
   `orders.recover_mercadolibre` (`allow_concurrent=False`).
10. **Antes de hacer commit**: `orders/apis.py` y la ruta `test/` de
    `orders/urls.py` exponen `import_falabella_orders` con `AllowAny`
    ("SOLO PRUEBA LOCAL — no hacer commit"), y `config/urls.py` ya
    incluye `orders.urls`. Al crear la ruta del webhook, esa vista de
    prueba debe salir de `orders/urls.py`.

### Pruebas

- Webhook: válido lanza el proceso; `application_id`/`user_id`/`topic`/
  `resource` inválidos responden `200` sin lanzar; responde sin llamar a
  Mercado Libre dentro del request.
- Proceso: pedido no pagado → no persiste; Full → no persiste; pagado →
  persiste y crea la orden; segunda notificación del mismo pedido ya
  creado → no hace nada; reclamo concurrente → solo uno llama
  `create_order`; pedido en `procesando` no se toca.
- Envío con varias órdenes (pack): con una orden aún sin llegar → no se
  procesa; completo → una sola orden de Shopify con todas las líneas y la
  misma bodega en todas las `MarketplaceOrder`; sin bodega que cubra el
  envío completo → novedad en todas y orden creada igual; la segunda
  notificación del pack no crea otra orden.
- `select_fulfillment_location`: el mismo SKU en dos líneas se suma
  (bodega con 1 unidad no cubre x1 + x1).
- Cliente según la rama elegida en la decisión C (C1: existente local,
  nuevo con creación en Shopify, sin datos de contacto; C2: cliente fijo y
  fallo temprano sin configurar).
- Recuperación: missed feeds procesados con la misma función; reintento de
  `error_creando_orden` solo de Mercado Libre.
- Falabella: `process_pending_orders` ignora pedidos de Mercado Libre;
  pruebas existentes en verde tras extraer `process_order`.

`python manage.py test orders integrations.mercadolibre` y
`python manage.py check`.

### Verificación en vivo

1. URL pública HTTPS (Railway) configurada como URL de notificaciones con
   el tópico `orders_v2`. En local no se puede recibir.
2. Observar notificaciones reales en `ProcessExecution` y confirmar la
   forma del payload.
3. Primer pedido real: revisar la orden en Shopify y el
   `MarketplaceOrder` en `/admin/`.

### Implementado (2026-09-24) y desviaciones del plan

- `orders/functions/process_shipment.py` (extraído de
  `process_pending_orders`; las pruebas de Falabella ahora mockean ahí).
- `orders/functions/parse_mercadolibre_notification.py`,
  `process_mercadolibre_order.py`, `process_mercadolibre_notification.py`,
  `recover_mercadolibre_orders.py`; `orders/webhooks.py`; ruta en
  `orders/urls.py`.
- `integrations/mercadolibre/functions/get_missed_feeds.py` y
  `get_connected_seller_id.py` (el webhook valida la cuenta leyendo solo
  la BD, sin demorar la respuesta).
- Migraciones `orders/0005_mercadolibre_fields.py` (`MERCADOLIBRE`,
  `procesando`, `shipment_id`, `customer_identification_type`,
  `customer_type`) y `orders/0006_seed_mercadolibre_process_types.py`.
- Desviación: **no hay `upsert_mercadolibre_order.py` aparte**; el guardado
  vive dentro de `process_mercadolibre_order.py`, porque webhook y
  recuperación ya comparten esa función completa.
- Desviación en la recuperación, caso 2 (aviso entregado, proceso
  fallido): en vez de leer `ProcessExecution` en error (que obligaría a
  `orders` a depender de los modelos internos del orquestador, contra su
  regla de bajo acoplamiento), el proceso crea un **marcador**
  `MarketplaceOrder` vacío antes de llamar a Mercado Libre y, si falla,
  deja el detalle en `error_description`. La recuperación trata los casos
  2 y 3 con la misma consulta local. Sin límite de reintentos: la
  recuperación corre pocas veces al día y un error permanente queda
  visible en cada ejecución.
- Un pedido sin `shipping.id` (venta sin envío) se procesa como su propio
  envío, sin chequeo de Full ni de ítems.
- Ante un error inesperado **después** del reclamo (p. ej. red al consultar
  inventario), las órdenes quedan en `procesando` y requieren revisión
  manual, aunque no se haya llegado a `create_order`. Conservador a
  propósito.

### Para ponerlo en marcha

1. `python manage.py migrate orders` (0005 y 0006) en el entorno público.
2. Crear el cliente fijo en Shopify y configurar
   `MERCADOLIBRE_SHOPIFY_CUSTOMER_ID`.
3. Desplegar y, en la app de Mercado Libre Developers, configurar la URL
   de notificaciones `https://<host>/api/orders/webhooks/mercadolibre/`
   con el tópico `orders_v2`.
4. Programar `orders.recover_mercadolibre` vía
   `POST /api/orchestrator/schedules/` (p. ej. 3–4 veces al día).
5. Primer pedido real: revisar la `ProcessExecution`, la orden en Shopify y
   el `MarketplaceOrder` en `/admin/`; confirmar la forma real del payload
   de la notificación y de `missed_feeds`.

## Riesgos

- Rotación del refresh token: si un proceso muere entre la respuesta y el
  guardado, la conexión queda inválida y hay que re-autorizar. Mitigado,
  no eliminable.
- Webhook sin firma (decisión B).
- Pedidos en `procesando` requieren revisión manual.
- Envío de pack que nunca se completa (una orden del pack no llega a
  pagarse o no se notifica): queda sin procesar; la recuperación
  (decisión G) debe reportarlo.
- SKU de Mercado Libre distinto del de Shopify (tabla de equivalencias
  pospuesta).
- Tokens en texto plano, igual que `SiigoToken`.

## Documentación a actualizar en el mismo cambio

- `docs/apps/orders.md`: Mercado Libre por webhook, `process_order`,
  estado `procesando`, `customer_identification_type`, recuperación.
- `docs/apps/integrations.md`, `docs/architecture/INTEGRATIONS.md`:
  proveedor `mercadolibre/`.
- `docs/patterns/PROVIDER_WEBHOOKS.md`: caso de proveedor sin firma.
- `docs/apps/orchestrator.md`: procesos nuevos.
- Si se elige C1: `APP_BOUNDARIES.md` y `docs/apps/customers.md`.
- `docs/INDEX.md`: fila de `orders`.
- Este plan: tabla del levantamiento de datos y decisiones cerradas.
