# Plan: carga de equivalencias de SKU por marketplace con validación en Shopify

Estado: **implementado (2026-10-02)**. Las decisiones las confirmó el
usuario el 2026-10-02, salvo las marcadas como "supuesto" en "Decisiones".
Antes del despliegue falta la prueba manual de solo lectura de la búsqueda
en Shopify (ver "Riesgos"). Lo que cambió respecto al diseño está en
"Notas de implementación".

## Contexto

`products` ya tiene `POST /api/products/equivalences/`
([`../apps/products.md`](../apps/products.md)). Esa carga recibe todas las
columnas de todos los marketplaces, crea el `Product` si no existe y **no
consulta Shopify**.

Se necesita una carga más estricta, pensada para que un usuario gestione los
SKU de **un marketplace a la vez**:

- Cada `sku_pamo` se valida contra Shopify.
- Las novedades se reportan fila por fila.
- El reporte queda guardado para descargarlo después.

La carga actual no se modifica.

## Objetivo

1. El usuario carga un Excel con `sku_pamo`, `sku_marketplace` y,
   opcionalmente, `ean`, e indica el marketplace.
2. La carga corre **siempre en segundo plano** con el orquestador. El
   frontend muestra una barra de progreso.
3. Al terminar, el usuario descarga el mismo Excel con una columna extra,
   `resultado`, por fila. Las cargas anteriores se consultan en un
   historial.
4. Shopify se consulta **por lotes de SKU**, no una vez por fila.

## Decisiones

| Tema | Decisión |
| --- | --- |
| Archivo | El frontend lee y arma el Excel (opción A). El backend recibe y devuelve filas JSON, igual que el resto de `products`. |
| Orden de validación | Primero Shopify, luego `Product`. |
| En Shopify y no en `Product` | Se crea el `Product` y la relación. |
| En `Product` y no en Shopify | Alerta, no se crea nada. |
| Ni en Shopify ni en `Product` | Novedad, no se crea nada. |
| Kits | Fuera de esta carga. Una fila cuyo `sku_pamo` es kit sale como novedad. Los kits tendrán su propio endpoint. |
| `sku_marketplace` ya apunta a otro producto | Se reasigna y se informa: `Reasignado: antes apuntaba a <sku>`. |
| EAN | Columna opcional `ean`. Con valor, se escribe. Si está vacía o no viene, **no se toca** el EAN existente. Esto es distinto de la carga de equivalencias, donde vacío borra. |
| Ejecución | Siempre en segundo plano. Sin límite de filas para ejecutar en línea. |
| Roles | `SKU_UPLOAD_ROLES = ["Admin"]`, separado de `CATALOG_ROLES`. Los roles nuevos se agregan ahí cuando se definan. |
| Correo | Fuera de alcance: el proyecto no tiene envío de correo configurado. |
| Límite de filas | **Supuesto:** 2000 por carga, como constante. |
| Historial | **Supuesto:** quien tiene el rol ve todas las cargas, no solo las suyas, porque el catálogo es compartido. |

## Hallazgos del código que condicionan el diseño

- `orchestrator/apis.py` restringe sus endpoints a `Admin` y `Operaciones`.
  El estado y el progreso se exponen desde `products`, para no atar los
  roles de esta carga a los del orquestador.
- El orquestador no guarda datos de negocio. El reporte lo guarda
  `products` en `SkuUpload`.
- `launch_process` responde `409` si el `ProcessType` no permite
  concurrencia y ya hay una ejecución activa. Se usa
  `allow_concurrent=True` con `max_concurrent_global=1`: una segunda carga
  queda en cola (FIFO) y no falla, y Shopify recibe una carga a la vez.
- `GET_VARIANT_BY_SKU` pide `first: 1` e incluye inventario por ubicación.
  Para validar existencia por lotes se necesita una consulta propia y
  liviana.

## Diseño

### 1. Consulta por lotes en Shopify (`integrations/shopify/`)

Es transporte genérico. No conoce marketplaces ni equivalencias.

- `queries.py`: `GET_VARIANTS_BY_SKUS`, con `productVariants(first: 250,
  after: $cursor, query: $query)`. Pide solo `id`, `sku` y
  `pageInfo { hasNextPage endCursor }`, sin inventario.
- `functions/get_variants_by_skus.py`: `get_variants_by_skus(skus) →
  {sku: variant_id}`.
  - Divide los SKU únicos en bloques. Valor inicial: 50 por bloque, como
    constante del módulo.
  - Arma la búsqueda con cada SKU entre comillas:
    `sku:"A" OR sku:"B"`. Las comillas y barras que traiga un SKU se
    escapan.
  - Recorre `pageInfo` hasta agotar los resultados. Shopify hace
    coincidencia parcial, así que un bloque puede traer más de 250
    variantes.
  - **Solo acepta coincidencias exactas** (`node.sku == sku`), la misma
    regla que `get_variant_by_sku`. Un SKU que no aparece no está en el
    diccionario.
  - Si un bloque falla (`ShopifyGraphQLError` o error HTTP), propaga la
    excepción. El llamador decide qué hacer.
- `get_variant_by_sku` **no cambia**: lo usan `create_order` y el comando
  `import_pamo_web_catalog`.

### 2. Modelo `SkuUpload` (`products/models.py`)

| Campo | Tipo | Nota |
| --- | --- | --- |
| `marketplace` | `CharField(choices=Marketplace.choices)` | |
| `uploaded_by` | FK a usuario, `SET_NULL`, nulo | |
| `execution_id` | `PositiveIntegerField`, nulo | Entero, no FK: `products` no importa modelos de `orchestrator`. |
| `rows` | `JSONField` | Filas de entrada tal como llegaron. |
| `results` | `JSONField`, nulo | Filas de entrada más `resultado` y `resultado_codigo`. |
| `summary` | `JSONField`, nulo | Conteo por código y por resultado. |
| `created_at` / `finished_at` | `DateTimeField` | |

Va con su migración y su registro en el admin, solo lectura.

### 3. Data migration del `ProcessType` (`products/migrations/`)

`code="products.upload_sku_equivalences"`, `allow_concurrent=True`,
`max_concurrent_global=1`. Mismo patrón que
`orders/migrations/0002_seed_process_type.py`.

### 4. Proceso de negocio (`products/functions/upload_sku_equivalences.py`)

`upload_sku_equivalences(params, progress_callback=None,
cancellation_token=None)` lee `params["upload_id"]`. Se registra en
`orchestrator/registrations.py`.

**Fase 1 — validar sin escribir (0–10 %):**

- Limpia las celdas con `normalize_cell`.
- Valida si hay vacíos y si se supera el largo máximo de `sku_pamo`,
  `sku_marketplace` y `ean`.
- Detecta duplicados dentro del archivo.
- Marca las filas cuyo `sku_pamo` es un kit en `Product`.

**Fase 2 — Shopify por lotes (10–60 %):**

- Junta los `sku_pamo` únicos de las filas que siguen vivas y llama a
  `get_variants_by_skus` bloque por bloque.
- Después de cada bloque, informa el progreso y revisa si se canceló la
  carga.
- Si un bloque falla, se reintenta una vez. Si vuelve a fallar, sus filas
  quedan como `Error consultando Shopify, reintentar` y la carga sigue con
  los demás bloques.

**Fase 3 — aplicar (60–100 %):**

- Una transacción por fila: crea el `Product`, crea, reasigna o actualiza
  el `MarketplaceSku` y escribe el EAN.
- Informa el progreso y revisa la cancelación cada N filas.

**Cierre:** en un `finally` guarda `results`, `summary` y `finished_at`,
también si hubo cancelación o error. Las filas que no alcanzaron a
procesarse quedan como `No procesado: carga cancelada`.

#### Resultado por fila

| # | Condición | Acción | `resultado` | código |
| --- | --- | --- | --- | --- |
| 1 | `sku_pamo` o `sku_marketplace` vacío, o algún valor supera el largo | — | `Error: <detalle>` | `error` |
| 2 | Par repetido, o el mismo `sku_marketplace` hacia otro `sku_pamo` en el archivo | — | `Duplicado en el archivo (fila N)` | `error` |
| 3 | `sku_pamo` es kit | — | `Es un kit: se gestiona en la carga de kits` | `error` |
| 4 | Falló la consulta a Shopify del bloque | — | `Error consultando Shopify, reintentar` | `error` |
| 5 | No está en Shopify y sí en `Product` | — | `Alerta: existe en Pamo pero no en Shopify` | `alert` |
| 6 | No está en Shopify ni en `Product` | — | `SKU no encontrado en Shopify` | `error` |
| 7 | En Shopify, no en `Product` | Crea `Product` y relación | `Exitoso: producto y relación creados` | `ok` |
| 8 | En Shopify, relación nueva | Crea relación | `Exitoso: relación creada` | `ok` |
| 9 | En Shopify, relación igual y EAN igual o no enviado | — | `Sin cambios: la relación ya existía` | `ok` |
| 10 | En Shopify, relación igual y EAN distinto | Actualiza EAN | `Exitoso: EAN actualizado` | `ok` |
| 11 | En Shopify, `sku_marketplace` apuntaba a otro producto | Reasigna (y EAN si viene) | `Reasignado: antes apuntaba a <sku>` | `alert` |
| 12 | Cancelación antes de procesar la fila | — | `No procesado: carga cancelada` | `error` |

El reporte conserva cualquier otra columna que traiga el Excel, sin tocarla.

### 5. Estado de la ejecución (`orchestrator/services.py`)

Se agrega una función pública de lectura,
`get_execution_status(execution_id) → {"status", "progress_percent",
"current_step", "error_message"}` o `None`. Es la única fuente del estado y
del progreso, así que `SkuUpload` no duplica esos campos.

### 6. API (`products/apis.py`, `products/urls.py`, `products/serializers.py`)

Todas usan `RoleRequiredMixin` con `allowed_roles = SKU_UPLOAD_ROLES`.

| Ruta | Entrada | Respuesta |
| --- | --- | --- |
| `POST /api/products/sku-uploads/` | `{marketplace, rows}` | `202 {id, execution_id}`. `400` si el marketplace no es válido, `rows` está vacío o supera el límite, o una fila no trae `sku_pamo` o `sku_marketplace` como columna. |
| `GET /api/products/sku-uploads/<id>/` | — | `id`, `marketplace`, `uploaded_by`, `created_at`, `finished_at`, `status`, `progress_percent`, `current_step`, `error_message`, `summary` y `rows` (`null` hasta terminar). `404` si no existe. |
| `GET /api/products/sku-uploads/` | `page` | Historial paginado: los mismos campos sin `rows`. |

El frontend hace polling sobre el detalle para la barra de progreso y, al
terminar, arma el Excel con `rows`. La validación de entrada vive en el
serializer y la lógica en `functions/`.

## Reutilización

- `get_variant_by_sku`: misma regla de coincidencia exacta. Se reutiliza la
  idea, no la consulta, que pide inventario y `first: 1`.
- `normalize_cell`, `Marketplace` y los largos máximos de los modelos.
- `RoleRequiredMixin`, `launch_process`, `progress_callback` y
  `cancellation_token`.
- Paginación de la app, con el mismo patrón que `ProductPagination`.

## Pruebas

`python manage.py test products orchestrator integrations` y
`python manage.py check`.

- **`get_variants_by_skus`** (HTTP simulado):
  - coincidencia exacta frente a parcial;
  - paginación con `hasNextPage`;
  - división en bloques;
  - escape de comillas;
  - SKU que no aparece;
  - el error se propaga.
- **Proceso** (Shopify simulado):
  - cada uno de los 12 casos;
  - carga mixta con `summary` correcto;
  - un bloque que falla dos veces no frena a los demás;
  - cancelación a mitad, que guarda lo parcial;
  - columnas extra conservadas;
  - EAN vacío no borra el existente.
- **API:**
  - `202` con `Admin`, que crea `SkuUpload` y llama a `launch_process`
    (simulado);
  - `403` sin sesión y sin rol;
  - `400` por marketplace inválido, `rows` vacío, límite superado o columna
    obligatoria faltante;
  - detalle con estado de `get_execution_status`;
  - `404`;
  - historial paginado.
- **`get_execution_status`:** ejecución existente e inexistente.

## Documentación a actualizar al implementar

- [`../apps/products.md`](../apps/products.md): nueva capacidad, modelo
  `SkuUpload`, y que esta carga **sí** valida contra Shopify.
- [`../apps/integrations.md`](../apps/integrations.md):
  `get_variants_by_skus`.
- [`../apps/orchestrator.md`](../apps/orchestrator.md): nueva línea en
  `registrations.py` y `get_execution_status`.
- [`../contracts/API.md`](../contracts/API.md): las tres rutas. Hay que
  coordinar con el frontend.
- Este plan: cambiar el estado a implementado.

## Riesgos

- **Sintaxis de búsqueda de Shopify.** El escape de SKU con caracteres
  especiales, el largo máximo de la búsqueda y el tamaño del bloque deben
  confirmarse contra la tienda real antes del despliegue. Es una prueba
  manual de solo lectura.
- **Cola en memoria por proceso.** Si el servidor se reinicia, la ejecución
  queda `INTERRUMPIDO` y `SkuUpload` queda sin `results`. El detalle lo
  muestra como interrumpido y el usuario vuelve a cargar el archivo.
- **Reasignación por error de tipeo.** Cambia cómo se traducen los pedidos.
  El código `alert` y el texto `Reasignado: antes apuntaba a…` permiten
  detectarlo en el reporte.
- **Concurrencia con otras escrituras** (admin, carga de equivalencias). La
  restricción única `(marketplace, sku)` protege la integridad. Un choque
  hace fallar solo esa fila, gracias a la transacción por fila.

## Notas de implementación (2026-10-02)

- **`max_concurrent_global` no funciona como se supuso.** En
  `concurrency_manager.submit` el valor se compara con el total de
  ejecuciones activas del orquestador, no con las de este tipo. Al liberar
  un cupo, la cola usa el límite global (3). Por eso la carga espera en cola
  si corre *cualquier* proceso (por ejemplo, la sincronización de Sodimac).
  Además, al salir de la cola podría correr junto a otra carga. No rompe
  nada: cada fila va en su transacción y la restricción única protege los
  datos. Solo deja de garantizar "una carga a la vez contra Shopify". No se
  cambió el orquestador; si hace falta, el arreglo va allá.
- `fila N` en "Duplicado en el archivo" es el número de fila del Excel,
  contando el encabezado (fila de datos 1 = fila 2).
- `uploaded_by` se devuelve como el email del usuario.
- Un error inesperado al aplicar una fila (por ejemplo, un choque con otra
  escritura) deja esa fila como `Error: <tipo>: <detalle>` y la carga
  sigue. Si falla la carga entera, las filas sin procesar quedan como
  `No procesado: la carga se detuvo por un error` (código `error`), y la
  ejecución termina en `ERROR`.
- `summary` = `{"total", "por_codigo": {ok, alert, error}, "por_caso": {...}}`.
  `por_caso` usa llaves estables (`producto_y_relacion`, `reasignado`,
  etc.) en vez del texto, que en `Reasignado` y `Duplicado` incluye datos de
  la fila.

## Fuera de alcance

- Endpoint de carga de kits (plan aparte).
- Notificación por correo.
- Definición de los roles nuevos.
- Migrar `import_pamo_web_catalog` a la consulta por lotes. Es posible, pero
  no hace falta.
