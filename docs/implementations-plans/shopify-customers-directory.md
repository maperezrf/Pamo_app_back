# Plan: directorio local de clientes de Shopify (app `customers`)

Estado: **implementado y probado** (modelo, funciones de
`integrations/shopify`, funciones de `customers`, webhook, migración de
datos para los `ProcessType`). `python manage.py test` completo en verde
(107 tests). Pendiente: suscribir el webhook en Shopify (requiere URL
pública, ver "Puntos abiertos"). Este documento resuelve un problema
descubierto durante la Fase 2 original de
`docs/implementations-plans/falabella-to-shopify-orders-import.md`: no se
puede buscar un cliente de Shopify por el campo `company` (donde vive la
cédula) a través de la API — ver esa sección para la evidencia completa. Este
plan reemplazó el enfoque de "tag de búsqueda" que se había considerado
primero.

## Objetivo

Mantener una tabla local sincronizada con los clientes de Shopify (id,
cédula/identificación desde `defaultAddress.company`, email, teléfono, id de
la dirección por defecto), para resolver "¿existe este cliente?" con una
consulta local en vez de depender de búsquedas en vivo contra la API de
Shopify. Reutilizable por cualquier canal que importe pedidos (Falabella y,
a futuro, Mercado Libre u otros) — no es una capacidad exclusiva de `orders`.

## Por qué es una app nueva, no parte de `orders` ni de `integrations`

- No es transporte/adaptación pura de un proveedor (eso es lo único que
  `integrations/shopify` debe contener, según
  `docs/architecture/INTEGRATIONS.md`: "nunca datos de negocio"). Este
  directorio persiste datos propios y tiene sus propias reglas de
  resolución de conflictos entre webhook y reconciliación.
- No es específico de `orders`: un canal futuro (Mercado Libre, etc.)
  necesitará la misma resolución de cliente por cédula sin pasar por
  `orders`.
- Antes de crear la app se revisó `docs/architecture/APP_BOUNDARIES.md`: el
  concepto no encaja en ninguna área existente (`accounts`=acceso,
  `integrations`=transporte técnico, `orchestrator`=ejecución técnica,
  `accounting`/`facturacion`/`logistics`=áreas de negocio ya definidas y sin
  relación con esto). Se documenta la nueva área en el mismo cambio.

## Modelo (`customers/models.py`)

```python
class ShopifyCustomer(models.Model):
    shopify_id = models.CharField(max_length=32, unique=True)  # sin prefijo gid://
    identification = models.CharField(max_length=32, blank=True, db_index=True)  # de company
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.CharField(max_length=254, blank=True, db_index=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    default_address_id = models.CharField(max_length=32, blank=True)  # sin prefijo
    shopify_updated_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
```

`identification` no es único a nivel de BD porque no está garantizado que lo
sea en los datos reales de Shopify (pudo haber captura manual duplicada o
sucia antes de este proyecto). La función de lookup decide qué hacer ante
más de un match (por ahora: loggear y usar el más reciente por
`shopify_updated_at`).

`default_address_id` se guarda localmente para poder **escribir** `company`
más adelante sin tener que leer primero la dirección en Shopify (ver método
de escritura, abajo) — evita una llamada extra por cada actualización.

## Sincronización — dos mecanismos, mismo destino

### 1. Webhook (tiempo real)

Sigue el patrón general de `docs/patterns/PROVIDER_WEBHOOKS.md` (léelo
primero — esta sección solo aplica ese patrón al caso de Shopify).

Archivos:

- `customers/webhooks.py`: `ShopifyCustomerWebhookView(APIView)`,
  `permission_classes = [AllowAny]` (Shopify no manda sesión de Django).
  Verifica la firma HMAC contra `request.body` **crudo** (antes de tocar
  `request.data`) vía un método nuevo
  `ShopifyClient.verify_webhook_signature(raw_body, header_signature)` —
  mismo patrón que `integrations/whatsapp/client.py::verify_signature`
  (`hmac.compare_digest`), adaptado a cómo firma Shopify: HMAC-SHA256 +
  **base64** (no hex como WhatsApp/Meta), header `X-Shopify-Hmac-Sha256`.
  Si la firma no valida: `403` sin persistir nada y sin lanzar ningún
  proceso. Si valida: llama a `orchestrator.services.launch_process(code="customers.process_webhook", params={"topic": ..., "payload": ...})`
  y responde `200` de inmediato — no procesa nada pesado dentro del
  request.
- `customers/functions/parse_customer_webhook.py`: normaliza el payload
  crudo (`customers/create`/`customers/update`) a los campos que nos
  interesan.
- `customers/functions/process_customer_webhook.py`: el callable
  registrado en `orchestrator` — llama al parser y después a
  `upsert_from_shopify_customer.py`. Mismo contrato que cualquier otro
  proceso: `(params, progress_callback=None, cancellation_token=None)`.
- `customers/functions/upsert_from_shopify_customer.py`: persiste/actualiza
  la fila en `ShopifyCustomer`. La reutiliza también la reconciliación
  (punto 2, abajo) — una sola función que sabe "cómo se guarda un
  cliente", sin duplicarla entre el webhook y el backfill periódico.

El `ProcessType` de este proceso (`code="customers.process_webhook"`)
**debe crearse con `allow_concurrent=True`** — pueden llegar varios
webhooks de Shopify casi al mismo tiempo, y el rechazo por defecto del
orquestador (409 si ya hay uno corriendo) está pensado para "no lances dos
veces el mismo reporte", no para eventos independientes concurrentes; con
`allow_concurrent=False` se perderían webhooks.

El registro de la llamada y el seguimiento de cada evento **no necesitan un
modelo nuevo**: cada `launch_process()` ya crea un `ProcessExecution` con
estado, progreso, error y timestamps — se reutiliza tal cual (ver
`docs/apps/orchestrator.md`).

Requiere una constante nueva en `config/constants.py`:
`SHOPIFY_WEBHOOK_SECRET` — **no es el mismo secreto que
`SHOPIFY_ACCESS_TOKEN`**, se consigue en la config de la app personalizada
del Shopify Admin de esta tienda.

Topics a suscribir: `customers/create`, `customers/update`.

**Punto abierto real**: no se verificó todavía la forma exacta del payload
JSON que Shopify manda en estos webhooks (formato REST del recurso
`customer`, presumiblemente con `default_address` anidado) — no adivinar el
parseo de `parse_customer_webhook.py`, confirmarlo contra un payload real o,
como mínimo, contra la documentación oficial de Shopify para el topic
exacto antes de programarlo.

### 2. Reconciliación programada (red de seguridad + backfill inicial)

`customers/functions/reconcile_from_shopify.py`, registrado en
`orchestrator` como proceso `customers.reconcile_shopify` (ver
`docs/apps/orchestrator.md` para el mecanismo de autorregistro). Pagina
`customers(first: 100, after: cursor)` — query ya probada en la
investigación real contra esta tienda (confirmado: 10.000 clientes, ~100
páginas) — y hace upsert de cada uno.

Programado a las 2am vía `ProcessScheduleConfig`
(`schedule_kind="DAILY"`, `run_at_time="02:00:00"`), como se documenta en
`docs/patterns/orchestrator-usage-guide.md` sección 4.

La primera corrida hace de **backfill inicial** — no se necesita un script
aparte para los clientes que ya tenían la cédula en `company` desde antes
de este proyecto. **Ejecutada en vivo el 2026-09-15**: 19.605 clientes
reales sincronizados en 157.6s (~100 páginas, sin throttling de Shopify),
de los cuales 5.314 ya tenían una cédula válida (6-10 dígitos numéricos) en
`company` — bastante más que la estimación inicial por muestreo (~230), que
subestimaba el tamaño real de la tienda (`customersCount` reportaba 10.000,
pero la paginación real llegó a 19.605).

## Funciones nuevas en `integrations/shopify`

Transporte/adaptación puros, sin persistencia — eso es trabajo de
`customers`. Todas verificadas por introspección real contra el schema de
esta tienda (misma API version que ya usa `CREATE_ORDER`).

Documentos GraphQL nuevos en `queries.py` (catalogar en `QUERIES.md`):

- `CREATE_CUSTOMER` (mutation `customerCreate`).
- `CREATE_CUSTOMER_ADDRESS` (mutation `customerAddressCreate`).
- `UPDATE_CUSTOMER_ADDRESS` (mutation `customerAddressUpdate`).
- `LIST_CUSTOMERS_PAGE` (query `customers(first, after)` paginada).

Funciones:

- `functions/create_customer.py`: `customerCreate` — solo datos del
  cliente (email/phone/nombre). Confirmado por introspección que
  `CustomerInput` **no tiene ningún campo de dirección** — la cédula no se
  puede fijar en esta misma llamada.
- `functions/create_customer_address.py`: `customerAddressCreate(customerId, address, setAsDefault=True)`
  — escribe la cédula en `company` de un cliente que **todavía no tiene**
  una dirección.
- `functions/update_customer_address.py` — **el método de escritura que
  pediste añadir**: `customerAddressUpdate(customerId, addressId, address, setAsDefault=True)`,
  para escribir/actualizar `company` (o cualquier otro campo de
  `MailingAddressInput`: `address1/2`, `city`, `countryCode`, `firstName`,
  `lastName`, `phone`, `provinceCode`, `zip`) en un cliente que **ya tiene**
  una dirección por defecto. Recibe `address_id` explícito — por eso el
  modelo local guarda `default_address_id`, para no tener que leer Shopify
  antes de poder escribir.
- `functions/list_customers_page.py`: pagina `LIST_CUSTOMERS_PAGE`, la usa
  la reconciliación.

## Funciones de orquestación en `customers`

- `customers/functions/find_by_identification.py`: lookup local simple
  (`ShopifyCustomer.objects.filter(identification=...)`).
- `customers/functions/set_identification.py`: decide cuál de las dos
  mutaciones de dirección usar — si el `ShopifyCustomer` local tiene
  `default_address_id`, llama a `update_customer_address`; si no, llama a
  `create_customer_address`. Actualiza la fila local con el resultado en
  ambos casos. Esta es la función que expone la capacidad de "escribir la
  cédula en `company`" al resto del sistema (`orders` y canales futuros).

## Impacto en la Fase 3 de `orders` (sin cambios de fondo, solo de origen del dato)

El flujo de importación deja de buscar en Shopify: llama a
`customers.find_by_identification(cedula)`. Si no existe, crea el cliente
(`create_customer` + `create_customer_address`) y hace upsert local
inmediato (no espera al webhook, por si se demora o falla). El resto del
plan de `orders` en el documento maestro no cambia.

## Documentación a actualizar en el mismo cambio

- `docs/architecture/APP_BOUNDARIES.md`: **hecho** — fila nueva `customers`.
- `docs/apps/customers.md`: **hecho** — expediente nuevo.
- `docs/apps/integrations.md` y `docs/architecture/INTEGRATIONS.md`:
  **hecho** — las funciones nuevas de Shopify.
- `integrations/shopify/QUERIES.md`: **hecho** — los 4 documentos GraphQL
  nuevos.
- `docs/patterns/PROVIDER_WEBHOOKS.md`: **hecho** — patrón nuevo, general
  para cualquier webhook de proveedor (no solo Shopify), con fila propia en
  `docs/INDEX.md`.
- `docs/INDEX.md`: **hecho** — fila nueva para `customers`.

## Puntos abiertos que exigen verificación en vivo antes de programar

1. Forma exacta del payload JSON de los webhooks `customers/create` /
   `customers/update` de Shopify para esta cuenta/API version — **sigue
   abierto**. `parse_customer_webhook.py` está escrito asumiendo la forma
   estándar del recurso REST `customer` de Shopify, sin confirmar contra un
   evento real (no hay URL pública para recibir uno todavía).
2. Registrar la suscripción del webhook (`webhookSubscriptionCreate`)
   requiere una URL pública HTTPS — **sigue abierto**, no disponible en
   desarrollo local sin túnel (ngrok o similar). Esto bloqueó probar el
   mecanismo de webhook end-to-end, pero no bloqueó construirlo (probado
   con payloads simulados) ni bloqueó la reconciliación, que sí se probó
   100% en local contra la cuenta real (19.605 clientes reales
   sincronizados el 2026-09-15, ver "Fase 2" arriba).
3. Confirmar que `SHOPIFY_WEBHOOK_SECRET` es un secreto distinto de
   `SHOPIFY_ACCESS_TOKEN` y dónde se consigue en el Shopify Admin de esta
   tienda — **sigue abierto**. La constante ya existe en
   `config/constants.py`/`.env`/`.env.example`, pero vacía en `.env` (no se
   consiguió el valor real todavía).
4. ~~Formato exacto del id de `MailingAddress` (`gid://shopify/MailingAddress/...`,
   posible sufijo `?model_name=CustomerAddress`)~~ — **resuelto por
   diseño, no por verificación**: se decidió tratarlo siempre como opaco
   (nunca se parsea ni se reconstruye), así que el formato exacto deja de
   importar. Ver `default_address_id` en `customers/models.py` y las notas
   en `integrations/shopify/functions/create_customer_address.py`.
