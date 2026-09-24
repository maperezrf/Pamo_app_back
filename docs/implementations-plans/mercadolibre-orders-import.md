# Plan: pedidos de Mercado Libre a Shopify por webhook

Estado: **fase 0 implementada (2026-09-24)** — conexión, conexión de la
cuenta desde el navegador (`connect/` + `callback/`) y funciones de
lectura, con 25 pruebas sin red en verde
(`python manage.py test integrations.mercadolibre`). Pendiente de la fase
0: crear la app en Mercado Libre Developers, `migrate` de
`integrations/migrations/0002_mercadolibretoken.py`, autorizar la cuenta y
hacer el levantamiento de datos. Fase 1 sin empezar. Reutiliza el modelo
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

Fuera de ambas fases: etiquetas/guías de Mercado Envíos, mensajería, reclamos, devoluciones, agrupación de packs y
sincronización de inventario hacia Mercado Libre.

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
  `get_billing_info.py`, `get_shipment.py`, cada una con `raw`.
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

| Campo | Endpoint | Presencia | Formato / observación |
| --- | --- | --- | --- |
| _pendiente_ | | | |

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

## Decisión C — clientes en Shopify (se toma al cerrar la fase 0)

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
            upsert MarketplaceOrder + ítems (get_or_create)
            reclamar el pedido (atómico) → process_order(order, customer)
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
- **E. Una `MarketplaceOrder` por orden** de Mercado Libre, no por pack.
  Limitación: un pack de dos órdenes genera dos órdenes en Shopify.
- **F. Concurrencia por pedido**. El `ProcessType` necesita
  `allow_concurrent=True` (regla de `PROVIDER_WEBHOOKS.md`), pero Mercado
  Libre manda varias notificaciones del mismo pedido casi a la vez
  (p. ej. pagado + actualizado). Sin control, dos procesos crearían la
  misma orden dos veces en Shopify. Propuesta:
  - `get_or_create` por `(marketplace, marketplace_order_id)` (el
    `UniqueConstraint` ya existe) para la fila;
  - estado nuevo `Status.PROCESSING = "procesando"` y reclamo atómico
    `MarketplaceOrder.objects.filter(pk=..., shopify_order_id="",
    status__in=[PENDING, ERROR_ORDER]).update(status=PROCESSING)`; solo
    el proceso que obtiene `1` sigue, los demás terminan sin hacer nada.
  - Un pedido que queda en `procesando` (proceso muerto a mitad de
    `create_order`) **no se reintenta solo**: es justo el caso de
    resultado desconocido del riesgo de duplicado abierto en
    `marketplace-orders-import.md`. Se revisa a mano en `/admin/`. Para
    Mercado Libre esto resuelve ese riesgo; para Falabella no cambia nada.
- **G. Recuperación**. Sin escaneo de pedidos, hay dos huecos: una
  notificación perdida y un pedido que falló (p. ej. SKU no encontrado,
  que no recibe otra notificación). Proceso programado
  `orders.recover_mercadolibre` (pocas veces al día, `allow_concurrent=False`):
  1. lee `/missed_feeds` del tópico `orders_v2` y procesa cada id con la
     misma función del webhook;
  2. reintenta los `MarketplaceOrder` de Mercado Libre en
     `error_creando_orden` (datos locales, no escanea Mercado Libre).
  Confirmar si se quiere; sin él, los pedidos con error quedan hasta que
  alguien los relance.

### Cambios

1. **`orders/models.py`** + migración: `Marketplace.MERCADOLIBRE`,
   `Status.PROCESSING`, `customer_identification_type`.
2. **`orders/functions/process_pending_orders.py`**: extraer
   `_process_one_order` como función pública `process_order(order,
   customer_id)` (o a su propio archivo) para que la usen el lote de
   Falabella y el proceso de Mercado Libre sin duplicar resolución de
   SKU, bodega y `create_order`. `process_pending_orders` filtra por
   `marketplace=FALABELLA` para no tomar pedidos de Mercado Libre.
   `_order_note` usa `customer_identification_type` si viene, con `"CC"`
   por defecto.
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

## Riesgos

- Rotación del refresh token: si un proceso muere entre la respuesta y el
  guardado, la conexión queda inválida y hay que re-autorizar. Mitigado,
  no eliminable.
- Webhook sin firma (decisión B).
- Pedidos en `procesando` requieren revisión manual.
- Packs partidos (decisión E) y SKU de Mercado Libre distinto del de
  Shopify (tabla de equivalencias pospuesta).
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
