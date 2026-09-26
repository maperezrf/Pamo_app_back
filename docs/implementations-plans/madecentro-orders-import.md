# Plan: pedidos de Madecentro (Shipturtle)

Estado: **fase 0 implementada (2026-09-24), sin desplegar**. Webhook de
captura, cliente y funciones de lectura listos, con pruebas sin red en
verde (`python manage.py test integrations.madecentro orders`, salvo la
falla conocida del registro en memoria del orquestador). Pendiente:
- desplegar;
- registrar la URL en Shipturtle;
- poner `MADECENTRO_ORDERS_TOKEN` en Railway.

Verificación real de solo lectura hecha el 2026-09-24; los hallazgos están
abajo. Madecentro es
un marketplace que opera sobre la plataforma Shipturtle: los webhooks y la
API de pedidos son de Shipturtle, pero la cuenta y los tokens son de
Madecentro. Este plan cubre solo la fase 0 (captura y conexión); las
decisiones de negocio se toman después, con datos reales.

## Objetivo y fases

- **Fase 0 — captura y conexión (solo lectura, este plan)**.
  1. Endpoint de webhook de pedidos que **solo registra** en los logs de
     Railway lo que llega (headers y body) y responde `200`. No lanza
     procesos, no persiste, no toca Shopify.
  2. Cliente de la API de Shipturtle y dos funciones de lectura
     (`get_order`, `list_orders`) que devuelven el JSON sin normalizar.
- **Fase 1 — importación a Shopify (pendiente, otro plan o ampliación de
  este)**. Se decide con lo que muestre la fase 0.

Fuera de la fase 0: webhooks y API de productos (tienen su propio token;
irán en otra URL y otra app, no en `orders`), modelo `MarketplaceOrder`,
procesos del orquestador, recuperación, cliente fijo de Shopify.

## Lo que se sabe hoy

- API base: `https://api.shipturtle.com/api/v1`, autenticación
  `Authorization: Bearer <token>`.
- El token **no vence** y es **uno por dominio**: uno para pedidos y otro
  para productos. En la fase 0 solo se usa el de pedidos.
- Pedido por id: `GET /orders/{id}`. Id de prueba confirmado por el
  usuario: `17707107`.
- Listado: `GET /all-orders/fetchData` con los parámetros
  `query` (JSON codificado en la URL, por ejemplo
  `{"order_date":{"startDate":"11/1/2023","endDate":"11/16/2023"},"type_of_order":"forward order"}`,
  fechas en `M/D/YYYY`), `limit`, `ascending` (`0` = descendente), `page`
  y `byColumn=1`.
- Webhooks configurables en Shipturtle: order create/update y product
  create/update. La pantalla de configuración **no ofrece secreto ni
  firma**. Queda por confirmar, con el primer aviso real, si llega algún
  header de firma.
- URL registrada (o a registrar) en Shipturtle para pedidos:
  `https://pamoappback-production.up.railway.app/api/orders/webhooks/madecentro/`.

## Parte 1 — webhook de captura

### Archivos

| Archivo | Cambio |
| --- | --- |
| `orders/webhooks.py` | Nueva `MadecentroOrderWebhookView(APIView)`, `permission_classes = [AllowAny]`, junto a `MercadoLibreOrderWebhookView`. |
| `orders/urls.py` | `path("webhooks/madecentro/", MadecentroOrderWebhookView.as_view(), name="orders-madecentro-webhook")`. |
| `orders/tests.py` | Pruebas del endpoint (ver abajo). |

### Comportamiento

- Lee `request.body` (bytes crudos), **no** `request.data`: el cuerpo
  podría no ser JSON válido y, si más adelante aparece una firma, se
  calcula sobre los bytes exactos (regla de
  [`PROVIDER_WEBHOOKS.md`](../patterns/PROVIDER_WEBHOOKS.md)).
- Registra una sola línea con `logging.getLogger(__name__)`:
  método, `Content-Type`, **todos los headers** (necesario para descubrir
  una firma) y el body decodificado como UTF-8 con `errors="replace"`,
  **truncado a 20 KB** para que un envío grande o malicioso no llene los
  logs.
- **Nivel `warning`, no `info`**: el proyecto no define `LOGGING` en
  `settings.py`, así que Python solo imprime en stdout/stderr desde
  `WARNING`. No se agrega configuración de logging para una captura
  temporal.
- Responde siempre `200` sin cuerpo. No valida, no lanza proceso y no
  persiste: este endpoint no procesa nada hasta la fase 1.
- Queda marcado en el docstring como **temporal de fase 0**.

### Riesgo aceptado

El endpoint es público y los logs de Railway contendrán datos del
comprador (nombre, dirección, documento) mientras dure la captura. El
usuario lo aceptó el 2026-09-24. Mitigación: tope de tamaño y retiro del
log de payload completo en la fase 1.

### Pruebas (sin red)

- `POST` con JSON → `200`, y el log (`assertLogs("orders.webhooks",
  level="WARNING")`) contiene un header y un campo del body.
- `POST` con cuerpo no JSON → `200` sin excepción.
- `POST` con body mayor a 20 KB → el log queda truncado.
- Petición anónima aceptada (sin sesión ni `X-API-Key`). Este endpoint no
  tiene caso de "rechazo por permisos" porque es `AllowAny` por diseño; el
  patrón de webhooks lo justifica.

## Parte 2 — cliente de la API de Shipturtle

### Ubicación y nombre

`integrations/madecentro/`, con la misma estructura que los demás
proveedores ([`INTEGRATIONS.md`](../architecture/INTEGRATIONS.md)). El
nombre es el del marketplace porque la cuenta y los tokens son de
Madecentro. Si otro marketplace llega por Shipturtle, se reevalúa el
nombre.

### Configuración (`config/constants.py`)

Nueva sección `# MADECENTRO (integrations/madecentro/) -- API de Shipturtle, Bearer fijo`:

- `MADECENTRO_API_BASE_URL = config("MADECENTRO_API_BASE_URL", default="https://api.shipturtle.com/api/v1")`
- `MADECENTRO_ORDERS_TOKEN = config("MADECENTRO_ORDERS_TOKEN", default="")`

Con `default=""`, como Mercado Libre: el proyecto arranca aunque la
variable no exista en un entorno. `MADECENTRO_PRODUCTS_TOKEN` **no se crea
todavía**. Los valores se ponen en `.env` local y en Railway, nunca en
código ni documentación.

### Archivos

| Archivo | Contenido |
| --- | --- |
| `integrations/madecentro/__init__.py` | Vacío. |
| `integrations/madecentro/client.py` | `MadecentroAPIError` y `MadecentroClient`. |
| `integrations/madecentro/functions/__init__.py` | Vacío. |
| `integrations/madecentro/functions/get_order.py` | `get_order(order_id)`. |
| `integrations/madecentro/functions/list_orders.py` | `list_orders(start_date, end_date, *, page=1, limit=50, type_of_order="forward order")`. |
| `integrations/madecentro/tests.py` | Pruebas del cliente y de las funciones con `requests` mockeado. |

No hay `models.py`: el token es fijo y no hay estado de conexión que
guardar.

### `MadecentroClient` (transporte puro)

Se toma como referencia `integrations/envia/client.py`, que es el cliente
Bearer más completo del repositorio. No se reutiliza como clase porque su
lógica está atada a Envía.

- `__init__(token)`: recibe el token para que el de productos use el
  mismo cliente más adelante sin cambiarlo. Si viene vacío →
  `MadecentroAPIError("MADECENTRO_TOKEN_MISSING")`.
- `get(path, params=None)` hace `requests.get(f"{MADECENTRO_API_BASE_URL}{path}", ...)`
  con:
  - headers `Authorization: Bearer …` y `Accept: application/json`;
  - timeout explícito `(5, 30)`;
  - `allow_redirects=False`: una redirección → error.
- Errores:
  - HTTP fuera de 2xx → `MadecentroAPIError(f"MADECENTRO_HTTP_{status}")`;
  - cuerpo que no es JSON → `MADECENTRO_RESPONSE_INVALID`.
- Nunca incluye el token ni el cuerpo completo en el mensaje de error.
- **Vacío a verificar**: si Shipturtle devuelve errores de negocio con
  HTTP 200 dentro del cuerpo (como Envía). El desarrollador lo comprueba en
  la lectura real y, si pasa, lo agrega a la validación del cliente.

### Funciones

- `get_order(order_id)` → `MadecentroClient(MADECENTRO_ORDERS_TOKEN).get(f"/orders/{order_id}")`.
  Devuelve el JSON tal cual.
- `list_orders(start_date, end_date, ...)`:
  - recibe `date`;
  - arma `query` con `json.dumps({"order_date": {"startDate": ..., "endDate": ...}, "type_of_order": ...})`,
    con fechas `f"{d.month}/{d.day}/{d.year}"` (sin ceros a la izquierda,
    como el ejemplo);
  - deja que `requests` codifique los parámetros;
  - usa `ascending=0` y `byColumn=1`;
  - devuelve el JSON tal cual.
- **La normalización a un dict propio se hace en la fase 1**, cuando se
  conozcan los campos. No se inventa hoy.

### Pruebas (sin red)

- Header `Authorization` correcto y URL armada con la base configurada.
- Token vacío → `MADECENTRO_TOKEN_MISSING` sin hacer la petición.
- 401/500 → `MADECENTRO_HTTP_<status>`, y el mensaje no contiene el token.
- Cuerpo no JSON → `MADECENTRO_RESPONSE_INVALID`.
- Redirección → error.
- `list_orders(date(2023, 11, 1), date(2023, 11, 16))` envía `query` con
  `11/1/2023` y `11/16/2023`, además de `page`, `limit`, `ascending=0` y
  `byColumn=1`.

### Verificación real (manual, solo lectura)

Con `MADECENTRO_ORDERS_TOKEN` en `.env`, desde `python manage.py shell`:

1. `get_order(17707107)`.
2. `list_orders(<hace 7 días>, <hoy>, limit=1)`.

Anotar en este plan (sin datos del comprador):

- las claves de primer nivel de cada respuesta;
- dónde viene el total o la paginación;
- el formato de un error, pidiendo un id inexistente.

#### Hallazgos de la verificación real (2026-09-24)

Pedido de prueba `17707107`, leído con `get_order` y `list_orders`:

- **Autenticación**: el token de pedidos funciona.
- **Envoltorio**:
  - `get_order` → `{"data": {...pedido...}}`.
  - `list_orders` → `{"data": [...], "count": N}`. `count` es el total del
    rango; se pagina con `page`/`limit`.
- **Errores**: un id inexistente devuelve HTTP `404` (`MADECENTRO_HTTP_404`).
  No se vio un error de negocio con HTTP 200, así que el cliente queda
  como está.
- **Identificadores del pedido**:
  - `id`: id interno de Shipturtle. Es el que usa `/orders/{id}`.
  - `order_id`: id del pedido en la tienda Shopify de Madecentro.
  - `name`: número visible, por ejemplo `#1001114236`.
  - `order_number`.
- **Estado**:
  - `financial_status` (`paid`), `confirmed`.
  - `status`: estado de Shipturtle, por ejemplo `New Orders`, junto con
    `status_sequence`.
  - `cancelled_at`, `cancellation_reason`.
  - `type_of_order` (`forward_order`, con guion bajo, aunque el filtro de
    `fetchData` usa `"forward order"`).
- **Tienda y vendedor**:
  - `store_id` (`3022`) y `line_items[].shop.name` (`Madecentro`) son la
    tienda del marketplace.
  - `company` (`id`, `company_name`, `brand_name`) parece ser la cuenta de
    vendedor dueña del token, **por confirmar**.
  - `vendor_id` viene `null` tanto en el pedido como en las líneas.
- **Líneas** (`line_items[]`):
  - `sku` **con el formato de Pamo** (`PMO-…`), `quantity`, `price` (COP,
    entero), `title`.
  - `variant_id` y `product_id` de Madecentro.
  - `fulfillment_status`, `vendor`.
- **Comprador**:
  - `billing_address` y `shipping_address` (`first_name`, `last_name`,
    `name`, `company`, `address1`, `address2`, `city`, `province`, `phone`).
  - `customer_email`, `customer_phone`, `customer_first_name`,
    `customer_last_name`.
  - **No se encontró un campo de documento (cédula o NIT)**. Pendiente
    para la factura en Siigo: puede venir en `note_attributes` (nulo en
    este pedido) o en `company`/`name` cuando es empresa.
- **Fechas**: `created_at` / `processed_at` en formato
  `YYYY-MM-DD HH:MM:SS` sin zona. `customer_address` trae `-05:00`, lo que
  sugiere hora de Colombia, **por confirmar**.
- **Otros**: `errors` (validaciones internas de Shipturtle para generar la
  guía) y `order_push_error` (`Vendor Shop not found`) son de la operación
  de Shipturtle y no afectan la lectura.

**Solo lecturas.** Recordatorio del plan de Mercado Libre: el `.env` local
apunta a la base de Railway. Esta fase no tiene migraciones, así que no
hay riesgo por esa vía.

## Fase 1a — rutina de importación (2026-09-25)

Estado: **implementada, sin desplegar**. Pruebas sin red en verde, salvo
la falla conocida del registro en memoria. Pendiente:
- configurar `MADECENTRO_SHOPIFY_CUSTOMER_ID` en Railway. Ya está en
  `.env` y se verificó en Shopify el 2026-09-25: el cliente existe y es
  distinto del de Falabella y Mercado Libre;
- desplegar, para que las migraciones `0007`/`0008` se apliquen con el
  código;
- primera corrida controlada con `{"limit": 1}`;
- programar `orders.import_madecentro` por la API del orquestador.

Mientras se conoce el webhook, una rutina del orquestador trae los pedidos
por API y los crea en Shopify. Después queda como recuperación de los
avisos que se pierdan, igual que `orders.recover_mercadolibre`.

### Decisiones

- **Cliente fijo** (confirmado por el negocio el 2026-09-25): todo a nombre
  de un mismo cliente de Shopify, `MADECENTRO_SHOPIFY_CUSTOMER_ID`, como
  Falabella y Mercado Libre. Sin él, la rutina falla antes de tocar nada.
- **Identificador**: `marketplace_order_id` = `id` de Shipturtle (el de
  `/orders/{id}`). `marketplace_order_number` = `name` sin `#`.
- **Qué se crea**: solo `financial_status == "paid"` y sin `cancelled_at`.
  Los `voided` (anulados; 7 de 13 en los últimos 30 días) se ignoran.
- **El listado es resumido**: sus líneas vienen sin SKU ni precio. Por eso
  se lee cada pedido pagado con `get_order`. Solo se lee si no está ya
  creado localmente.
- **Un pedido = un envío**: `process_shipment([pedido], cliente_fijo)`,
  que resuelve SKU, bodega y crea la orden.
- **Sin duplicados**: se reutiliza el reclamo atómico de Mercado Libre
  (`procesando`), movido a `orders/functions/claim_orders.py` para que lo
  usen los dos canales. Un pedido ya creado o en `procesando` no se toca;
  si el proceso muere en `procesando`, se revisa a mano, como en Mercado
  Libre. Así el webhook futuro y la rutina pueden coincidir sin crear dos
  órdenes.
- **Reintentos**: además del rango de días, la rutina reintenta los
  pedidos de Madecentro guardados en `pending`/`error_creando_orden`,
  aunque ya estén fuera del rango. Si al releer un pedido pendiente ya no
  está pagado, se descarta la fila.
- **Parámetros** (`params` del orquestador):
  - `days`: por defecto `1`, es decir de ayer a hoy en hora de Colombia
    (`fetchData` filtra por día, inclusive).
  - `limit`: máximo de pedidos a procesar en la corrida, para una primera
    corrida controlada.
- **Resultado**: un fallo en un pedido no detiene el resto. Al final, si
  hubo fallos, la ejecución termina en error con el resumen.
- **Riesgo conocido**: un pedido que se paga después de que su fecha salió
  del rango no se descubre. Se mitiga corriendo con un `days` mayor.

### Archivos

| Archivo | Cambio |
| --- | --- |
| `config/constants.py` | `MADECENTRO_SHOPIFY_CUSTOMER_ID` (default `""`). |
| `integrations/madecentro/functions/get_order.py` | Devuelve el pedido normalizado (id, número, estado de pago, cancelado, comprador, ítems). |
| `integrations/madecentro/functions/list_orders.py` | Devuelve `{"orders": [{order_id, order_number, financial_status}], "count"}`. |
| `orders/models.py` + migración | `Marketplace.MADECENTRO = "madecentro"`. |
| `orders/migrations/…_seed_madecentro_process_type.py` | `orders.import_madecentro`, `allow_concurrent=False`. |
| `orders/functions/claim_orders.py` | `claim_orders(marketplace, order_ids)` y `RETRYABLE_STATUSES`, extraídos de `process_mercadolibre_order`. |
| `orders/functions/process_madecentro_order.py` | Procesa un pedido de forma idempotente. Lo reutilizará el webhook. |
| `orders/functions/import_madecentro_orders.py` | El proceso registrado. |
| `orchestrator/registrations.py` | `register_process("orders.import_madecentro", …)`. |

## Documentación que actualiza el desarrollador

- `docs/apps/orders.md`: sección "Madecentro (fase 0)" con el endpoint de
  captura, marcado como temporal.
- `docs/apps/integrations.md`: agregar `madecentro/` a la lista de
  proveedores.
- `docs/architecture/INTEGRATIONS.md`: fila Madecentro, "REST Bearer fijo
  (API de Shipturtle), un token por dominio".
- `docs/INDEX.md`: la fila de `orders` menciona Madecentro (webhook en
  captura).
- Este plan: estado y hallazgos de la verificación real.

Validación final: `python manage.py check` y
`python manage.py test orders integrations.madecentro`. La falla conocida
del registro en memoria del orquestador no cuenta.

## Preguntas abiertas para la fase 1

Se responden con los datos capturados:

1. ¿El webhook trae el pedido completo o solo un puntero? ¿Llega algún
   header de firma?
2. ¿Qué identifica a nuestra cuenta de vendedor? Shipturtle es
   multivendedor: ¿un pedido puede traer líneas de otros vendedores?
3. ¿Qué estado significa "pagado" o "listo para crear en Shopify"? ¿Qué
   trae un update (cancelaciones, cambios de estado)?
4. ¿Dónde viene el documento (cédula o NIT) del comprador? La API trae
   nombre, dirección, email y teléfono, pero no se encontró un campo de
   documento. Es la fuente de la factura en Siigo.
5. **Resuelto el 2026-09-25 con el catálogo de `products`**:
   - equivalencia Madecentro `PMO-028-MP` → `PAC8424` (producto simple,
     existe en Shopify);
   - `process_shipment` traduce los SKU con esas equivalencias;
   - cada SKU nuevo de Madecentro se carga en `/api/products/equivalences/`
     (columna `madecentro_sku`);
   - un SKU que sea kit queda en error hasta definir el reparto de precio.

   Contexto original: el SKU
   del pedido real `PMO-028-MP` **no existe en Shopify**, y tampoco
   `PMO-028` ni `PMO028`. Mientras no se resuelva, la rutina deja esos
   pedidos en `error_creando_orden` ("SKU no encontrado en Shopify") y los
   reintenta en cada corrida, sin crear órdenes parciales. Falta que el
   negocio diga a qué producto de Shopify corresponde cada SKU de
   Madecentro, o si `-MP` es un kit (el modelo de composición de kits está
   pendiente). Con esa respuesta se decide entre corregir el SKU en
   Shipturtle o crear una tabla de equivalencias.
6. ¿Quién despacha: nosotros o Madecentro?
7. Cliente fijo `MADECENTRO_SHOPIFY_CUSTOMER_ID` y valor `MADECENTRO` en
   `MarketplaceOrder.marketplace`, siguiendo el patrón de Falabella y
   Mercado Libre.
