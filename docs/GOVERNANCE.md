# Governance de Pamo App — Backend

> Reglas de arquitectura, patrones y flujo de trabajo para este repositorio
> (`Pamo_app_back`, Django + DRF) — humano o agente de IA. El objetivo es que
> el resultado sea el mismo sin importar quién (o qué modelo) escribió el
> código. Cuando una regla de aquí choque con una preferencia puntual de una
> tarea, esta guía gana salvo decisión explícita en contrario del equipo,
> documentada en la sección 14.
>
> El frontend (`pamo_app_front`, React) tiene su propio `docs/GOVERNANCE.md`
> con las reglas equivalentes de su lado. Los principios generales (§2), la
> tabla de áreas de negocio (§3.2) y el contrato de API (§12) deben
> mantenerse consistentes entre los dos repos — si algo de eso cambia acá,
> se actualiza también en `pamo_app_front`.

## 1. Cómo usar este documento

Este documento tiene dos lectores distintos, y el objetivo es que ambos
lleguen al mismo resultado:

- **El desarrollador**: conoce patrones de Django/DRF, permisos, vistas,
  APIs — puede leer un diff y notar cuándo una IA se desvió de una
  convención.
- **El vibecoder**: dirige el desarrollo completamente a través de
  instrucciones a una IA, sin necesariamente leer el código línea por línea.

Para que el resultado no dependa de quién pidió el cambio, la regla es
simple: **toda funcionalidad nueva se le pide a la IA citando este
documento como contexto obligatorio** ("tenés que seguir
`docs/GOVERNANCE.md`") y, antes de dar por cerrada una tarea, se revisa
contra el checklist de la sección 13 — está escrito para poder revisarse
sin saber programar.

## 2. Principios generales

1. **Cambios mínimos y quirúrgicos.** Una tarea resuelve una tarea. No
   aprovechar un pedido para refactorizar algo no solicitado. Esto importa
   el doble cuando quien aprueba el cambio no va a leer el código línea por
   línea: un diff enfocado es revisable con el checklist de la sección 13;
   un diff que tocó cinco cosas no relacionadas, no.
2. **Aditivo por defecto.** Una funcionalidad nueva no elimina, oculta ni
   degrada endpoints, campos o datos existentes sin que se haya pedido
   explícitamente. Si algo se rompe, primero se recupera la versión anterior
   desde Git — no se reconstruye de memoria.
3. **La interfaz nunca es la barrera de seguridad.** Todo permiso se valida
   en el servidor, en cada capa que lo necesite — nunca alcanza con que el
   frontend oculte un botón. Un usuario puede llamar la API directamente sin
   pasar por ninguna pantalla, y ahora que frontend y backend son repos
   separados, el backend no puede asumir nada sobre qué validaciones hizo
   (o dejó de hacer) el otro lado.
4. **Una sola fuente de verdad por concepto.** Ni credenciales, ni permisos,
   ni el registro de áreas/módulos, ni la lógica de un proveedor externo
   pueden vivir duplicados en dos archivos. Si hace falta usarlo en dos
   lugares, se importa, no se copia.
5. **No hay código muerto.** Nada de vistas, imports o rutas comentadas
   "por si acaso". Si no se usa, se borra (Git conserva el historial).
6. **Español para negocio, inglés para lo técnico genérico**: nombres de
   modelos, mensajes de usuario y variables de dominio en español
   (`Pedido`, `Remision`, `numero_guia`); `request`, `response`, `save`,
   nombres de clases base de Django/DRF, en inglés. No mezclar dentro de un
   mismo identificador.

## 3. Organización del backend: apps por área de negocio

### 3.1 Qué es un "área"

Pamo es un marketplace: la operación se divide naturalmente en áreas de
negocio (Logística, Facturación, Publicaciones, Productos, Pedidos,
Contraentrega, Accesos y Seguridad, etc.). **Cada área de negocio es una app
de Django**, y cada app cubre un área — nunca dos apps para la misma área
("por las dudas" o porque el nombre no calzaba exacto), y nunca un área
repartida entre varias apps sin relación.

### 3.2 Registro de áreas y módulos

Tabla única de verdad — **debe mantenerse igual en el repo de frontend**
(`pamo_app_front/docs/GOVERNANCE.md` §3). Antes de crear una app nueva, se
consulta esta tabla; al crear una, se agrega una fila acá y se replica en el
otro repo en el mismo ciclo de trabajo.

| Área | App Django | Prefijo API | Responsabilidad | Estado |
|---|---|---|---|---|
| Accesos y Seguridad | `accounts` | `/api/auth/` | Login, allowlist, roles y permisos | Activa |
| Productos y Catálogo | `productos` | `/api/productos/` | Catálogo maestro, precios, márgenes | Pendiente |
| Publicaciones | `publicaciones` | `/api/publicaciones/` | Publicar/sincronizar catálogo en canales (Shopify, Falabella, Mercado Libre, etc.) | Pendiente |
| Pedidos | `pedidos` | `/api/pedidos/` | Captura, estados y trazabilidad de pedidos multicanal | Pendiente |
| Logística | `logistica` | `/api/logistica/` | Fulfillment, guías de envío, tracking, devoluciones | Pendiente |
| Facturación | `facturacion` | `/api/facturacion/` | Facturas, remisiones contables, integración Siigo | Pendiente |
| Contraentrega (COD) | `contraentrega` | `/api/contraentrega/` | Elegibilidad y gestión de pago contra entrega | Pendiente |
| Configuración e Integraciones | `configuracion` | `/api/configuracion/` | Estado de conexiones externas, ambientes, secretos (solo estado, nunca el valor) | Pendiente |

Esta tabla es un punto de partida, no un techo: se edita libremente a medida
que el negocio lo necesite. Lo que no se vale es crear una app fuera de esta
tabla sin agregarla primero.

### 3.3 Flujo obligatorio al pedir una funcionalidad nueva

1. Consultar la tabla del §3.2: ¿esta funcionalidad pertenece a un área ya
   existente?
2. Si pertenece a un área existente → se agrega el modelo/vista/función
   dentro de esa app. **No se crea una app nueva.**
3. Si no encaja en ninguna → se define el área nueva, se agrega a la tabla
   en el mismo cambio (nombre, app Django, prefijo, responsabilidad, estado)
   — y se replica en el repo de frontend — y recién ahí se crea la app.
4. Ante la duda, se pregunta antes de crear — nunca se crea una app paralela
   "por si acaso no calzaba".

**Ejemplo de cómo pedirlo** (para el vibecoder): en vez de "hazme un
endpoint para ver el estado de los envíos", mejor: *"Necesito un endpoint
para el área de Logística (ya existe la app `logistica`) que devuelva el
estado de los envíos. Seguí `docs/GOVERNANCE.md`."* Si no estás seguro de a
qué área pertenece algo, pedile a la IA que primero proponga el área
consultando la tabla del §3.2, antes de escribir una sola línea de código.

### 3.4 Estructura interna de una app

```
backend/
  logistica/                  # ejemplo: una app = un área
    models.py
    serializers.py
    urls.py
    views.py                  # delgada: permisos + parseo + llama a functions/
    functions/                 # toda la lógica de negocio vive acá
      __init__.py
      generar_guia.py
      calcular_costo_envio.py
    tests.py
```

- **`views.py` solo orquesta**: valida permisos (`RoleRequiredMixin`), parsea
  la petición, llama a una función de `functions/`, devuelve la respuesta
  serializada. No contiene reglas de negocio.
- **`functions/` contiene la lógica real**: un archivo por caso de uso (o
  agrupado por tema si son pocos), funciones con nombre explícito de lo que
  hacen. Es lo que se testea con `python manage.py test`.

```python
# logistica/functions/generar_guia.py
def generar_guia_envio(pedido):
    """Valida el pedido, pide la guía al proveedor de envíos y guarda el resultado."""
    ...

# logistica/views.py
from accounts.permissions import RoleRequiredMixin
from .functions.generar_guia import generar_guia_envio

class GenerarGuiaAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Logistica", "Admin"]

    def post(self, request, pedido_id):
        pedido = get_object_or_404(Pedido, id=pedido_id)
        guia = generar_guia_envio(pedido)
        return Response(GuiaSerializer(guia).data)
```

Motivo: separa "¿quién puede hacer esto?" (vista) de "¿qué hace esto?"
(función) — evita repetir el error más común en vistas que crecen sin
control: lógica de negocio, validación y llamadas a terceros todas
mezcladas en el mismo método hasta que el archivo se vuelve imposible de
tocar con seguridad.

## 4. Vistas y permisos

### 4.1 Vistas de API: siempre `APIView`

Toda vista que exponga datos como JSON es una subclase de
`rest_framework.views.APIView`. No usar `@api_view` de función ni
`ViewSet`/`ModelViewSet` salvo que una tarea futura justifique
explícitamente el CRUD genérico — por defecto, `APIView` explícito.

Motivo: un `APIView` obliga a declarar método por método (`get`, `post`,
…), lo que hace muy difícil "olvidar" el chequeo de permisos en una acción
puntual. Una ruta dinámica tipo catch-all puede dejar pasar una acción sin
el permiso correcto por diseño — un `APIView` con métodos explícitos no
tiene ese punto ciego.

Únicas vistas de función permitidas: endpoints triviales sin lógica de
negocio (ej. un endpoint de `csrf`).

### 4.2 Permisos: `Group` + `RoleRequiredMixin`, siempre explícito

Todo `APIView` que no sea público declara sus roles permitidos:

```python
from accounts.permissions import RoleRequiredMixin

class MiEndpoint(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Logistica"]

    def get(self, request):
        ...
```

- Sin `allowed_roles`, el endpoint exige sesión iniciada (cualquier usuario
  autorizado) — nunca se deja un endpoint sin `permission_classes` por
  omisión.
- Los roles son `Group` estándar de Django, gestionables desde `/admin/` —
  no se crea un sistema de roles paralelo (tabla propia, enum en código,
  lista de correos hardcodeada en un archivo). Un solo sistema de permisos,
  siempre: dos sistemas de autorización coexistiendo es la forma más común
  de terminar con un usuario que "según un endpoint tiene acceso y según
  otro no".
- Cuando un módulo necesite permisos más finos que "roles con nombre" (ej.
  un módulo de Remisiones con acciones separadas de ver/crear/confirmar/
  facturar), usar los permisos nativos de Django (`Permission` +
  `content_type`) asociados al `Group`, no un campo de texto libre ni un
  JSON de capacidades a mano.
- Si se agrega un módulo nuevo, registrarlo en la tabla del §3.2 — nunca
  dejar un `APIView` cuyos permisos no coincidan con lo documentado ahí.

**Endpoints públicos sin sesión** (ej. compartir una cotización con un
cliente que no está logueado) son válidos y esperados — no una excepción
vergonzosa — pero se construyen con reglas propias, no como un `APIView`
normal al que simplemente se le quitó el permiso:

- `permission_classes = [AllowAny]` explícito en la clase, con un
  comentario de una línea diciendo por qué es público.
- El recurso se busca por un token/UUID no adivinable en la URL, nunca por
  un ID incremental (`/cotizaciones/<uuid>/`, no `/cotizaciones/482/`).
- Alcance de solo lectura del recurso puntual — el mismo endpoint nunca
  también permite editar/listar otros recursos del mismo modelo.
- No reexpone datos más allá de lo que ese recurso puntual necesita
  mostrar.
- **Se documenta en el contrato de API (§12)** — el frontend no tiene forma
  de saber que una ruta es pública si no está anotado ahí.

### 4.3 Webhooks entrantes de terceros

Un webhook (Shopify, Envía, Siigo, Mercado Pago, etc. notificando un evento)
vive en la app del área dueña del recurso que notifica — **no** en
`integrations/` (esa carpeta es solo para llamadas salientes) y **no**
mezclado en `views.py`. Cada app que recibe webhooks tiene un archivo
`webhooks.py` propio, paralelo a `views.py`:

```
backend/
  pedidos/
    views.py       # API que consume el frontend (RoleRequiredMixin)
    webhooks.py    # endpoints que llaman los proveedores externos
    functions/
      procesar_webhook_shopify_order.py
```

Reglas:

1. **Un evento → el área dueña del recurso.** Ej.: `orders/create` de
   Shopify va en `pedidos/webhooks.py`; `products/update` de Shopify en
   `productos/webhooks.py` o `publicaciones/webhooks.py` (según cuál sea el
   dueño del dato); notificación de tracking de Envía en
   `logistica/webhooks.py`; notificación de pago de Mercado Pago en
   `facturacion/` o `pedidos/`, el que corresponda. Si un evento afecta a
   dos áreas, el webhook vive en la dueña del recurso y esa área llama al
   `functions/` de la otra si hace falta — misma orquestación normal entre
   áreas, nada especial por ser webhook.
2. **No usan `RoleRequiredMixin`.** Son `AllowAny` por definición — quien
   llama es un servidor externo, no un usuario con sesión — pero la
   autenticación no desaparece, se reemplaza por verificación de firma.
3. **La verificación de firma/HMAC vive en `integrations/<proveedor>.py`**,
   no en la vista — es conocimiento específico del proveedor, igual que
   cualquier otro método de ese cliente:

   ```python
   # integrations/shopify.py
   class ShopifyClient:
       ...
       def verify_webhook_signature(self, raw_body, header_hmac): ...
   ```

   ```python
   # pedidos/webhooks.py
   from integrations.shopify import ShopifyClient
   from .functions.procesar_webhook_shopify_order import procesar_webhook_shopify_order

   class ShopifyOrderWebhook(APIView):
       permission_classes = [AllowAny]

       def post(self, request):
           firma_ok = ShopifyClient().verify_webhook_signature(
               request.body, request.headers.get("X-Shopify-Hmac-Sha256")
           )
           if not firma_ok:
               return Response(status=401)
           procesar_webhook_shopify_order.delay(request.data)
           return Response(status=200)
   ```
4. **Responder rápido, procesar async.** El proveedor espera un 2xx en
   pocos segundos o reintenta (a veces duplicando la entrega) — la vista
   solo valida la firma y encola una tarea de Celery (§9); nunca hace el
   trabajo pesado en línea.
5. **Idempotencia obligatoria.** Un mismo evento puede llegar más de una vez
   (reintento del proveedor) — la función de `functions/` que procesa el
   webhook debe poder correr dos veces con el mismo payload sin duplicar
   datos (usar el ID de evento/idempotency key del proveedor como clave de
   deduplicación, mismo principio que §9).
6. **No es el mismo patrón que los "endpoints públicos" del §4.2.** Ese es
   para que un humano sin sesión vea un recurso puntual (de ahí el token/UUID
   no adivinable en la URL); un webhook es máquina-a-máquina y su seguridad
   real es la verificación de firma, no la URL — alcanza con una ruta
   convencional (`/api/pedidos/webhooks/shopify/orders/`).

## 5. Conexiones con terceros: el pool de integraciones

### 5.1 Qué va aquí y qué no

Un único paquete central concentra toda la comunicación con proveedores
externos (Shopify, Sodimac, Falabella, Mercado Libre, Envía, Siigo, etc.):

```
backend/
  integrations/
    __init__.py
    shopify.py         # class ShopifyClient
    sodimac.py          # class SodimacClient
    falabella.py        # class FalabellaClient
    mercadolibre.py      # class MercadoLibreClient
    envia.py             # class EnviaClient
    siigo.py              # class SiigoClient
```

**Regla dura**: ningún `functions/` de ninguna app importa el SDK de un
proveedor ni usa `requests` para hablar con él directamente. Siempre pasa
por el cliente correspondiente en `integrations/`. Si dos áreas necesitan el
mismo proveedor (ej. Logística y Publicaciones ambas usan Shopify),
**comparten el mismo cliente** — nunca se duplica la conexión en dos
lugares.

### 5.2 Estructura de cada cliente

Cada archivo expone una clase con métodos nombrados por lo que hacen (no
genéricos `get`/`post`), y lee sus credenciales únicamente desde
`config/constants.py`:

```python
# integrations/shopify.py
class ShopifyClient:
    def get_products(self, cursor=None): ...
    def update_price(self, sku, price): ...
    def create_order(self, payload): ...
```

```python
# logistica/functions/calcular_costo_envio.py
from integrations.envia import EnviaClient

def calcular_costo_envio(pedido):
    cliente = EnviaClient()
    return cliente.cotizar(pedido.destino, pedido.peso)
```

Un proveedor nuevo se agrega como un archivo nuevo en `integrations/`, sin
tocar los clientes existentes de otros proveedores.

### 5.3 Mensajería con clientes (WhatsApp, Telegram, etc.)

**Nunca automatización por DOM sobre una sesión web humana logueada.** El
patrón permitido es: *preparar servidor-side (número, texto, enlace) → abrir
el enlace → confirmación humana del envío*. Vive como cualquier otro
cliente en `integrations/` (ej. `integrations/whatsapp.py` con un método
`preparar_mensaje(...)` que arma la URL `wa.me`/`api.whatsapp.com` con el
texto y el destinatario) — nunca dispara el envío por sí solo.

Envío verdaderamente automático (sin clic humano) solo se construye contra
la API oficial del proveedor (ej. WhatsApp Business Cloud API de Meta, con
número de negocio verificado) — nunca controlando un navegador logueado
como si fuera un usuario. Motivo: automatizar una sesión web humana depende
del DOM de la página (se rompe con cualquier cambio de interfaz) y suele
violar los términos de servicio del proveedor — riesgo real de bloqueo de
la cuenta/número.

## 6. Configuración y secretos

- Toda credencial o URL externa se declara en un único módulo de constantes
  (`backend/config/constants.py`), leído con `python-decouple` desde
  variables de entorno. **Nunca** `os.environ` directo desde otro archivo,
  **nunca** un valor hardcodeado, ni siquiera temporalmente para probar.
- Un secreto nuevo se documenta en `.env.example` en el mismo commit que lo
  introduce.
- Los endpoints y respuestas nunca exponen el valor de un secreto — a lo
  sumo su estado ("Configurado" / "Ausente").
- Ningún secreto de este repo (tokens de proveedores, `SECRET_KEY`,
  credenciales de base de datos) se comparte nunca con el repo de frontend
  — el frontend solo conoce la URL base de la API, nada más.

## 7. Modelos y migraciones

- `PascalCase` para modelos, consistente en todo el proyecto — no mezclar
  convenciones entre apps.
- Toda modificación de modelo genera su migración (`makemigrations`) en el
  mismo commit/tarea que la origina — nunca se acumulan migraciones
  pendientes sin aplicar en el historial del repo.
- Migraciones aditivas mientras el producto esté en fase de adopción interna
  (agregar columnas/tablas, no romper las existentes) — un cambio
  destructivo (drop de columna/tabla) solo con autorización explícita y
  backup confirmado antes de ejecutarlo.

## 8. Serializers

- Un `Serializer`/`ModelSerializer` de DRF por entidad expuesta — no
  serializar a mano con diccionarios sueltos dentro de la vista, salvo
  respuestas triviales de 1-2 campos (ej. `{"detail": "ok"}`).
- Validación de negocio (no solo de tipo) vive en el serializer
  (`validate_<campo>` / `validate`), no en la vista.
- La forma de cada respuesta (nombres de campo, tipos) es parte del
  contrato con el frontend (§12) — un cambio de nombre o de tipo en un
  serializer existente es un cambio de contrato, no un detalle interno.

## 9. Tareas en segundo plano y programadas: Celery + Celery Beat + Redis

Todo trabajo en background (importaciones, sincronizaciones, disparo de un
RPA, y cualquier tarea programada) se implementa con **Celery**, broker
**Redis**, y **Celery Beat** para lo periódico. No usar hilos manuales,
`threading`, `subprocess` sueltos ni un scheduler casero.

Motivo: es el estándar del ecosistema Django (integra con el ORM sin
adaptadores), da reintentos/backoff nativos, y evita depender de un
disparador de cron externo sin autenticación de servicio apuntando a un
endpoint público — cualquier tarea programada vive autenticada dentro del
propio backend, auditable igual que cualquier otra tarea.

### Contrato de estados

Toda tarea (Celery o no) reporta su progreso con el mismo contrato:

```
Pendiente → En cola → Procesando → Completado
                                 → Completado con observaciones
                                 → Fallido → Reintentando
                                 → Cancelado
```

Con progreso e historial consultable desde la pantalla correspondiente
(vía un endpoint que el frontend puede sondear — documentado en §12), y
**idempotencia real**: correr el mismo job dos veces con el mismo insumo no
duplica datos ni reescribe algo ya procesado sin cambios.

### Despliegue en Railway

Tres servicios sobre el mismo repo, compartiendo `REDIS_URL`/`DATABASE_URL`
del proyecto Railway:

```
web:    gunicorn config.wsgi
worker: celery -A config worker -l info
beat:   celery -A config beat -l info
```

Redis se agrega como servicio de base de datos del proyecto Railway (template
de un clic), no como dependencia externa nueva a administrar. **Antes de
habilitarlo en producción**: confirmar que el plan de Railway soporta los
servicios adicionales — no asumir capacidad ilimitada para agregar Redis +
worker + beat sobre la marcha.

### Cuándo NO usar Celery

Una tarea puntual, síncrona y rápida (< 1-2s, ej. validar un webhook) va
directo en la vista — no crear una task de Celery para todo por costumbre.
Celery es para lo que es genuinamente asíncrono, largo, o programado.

## 10. Testing

- Todo `APIView` nuevo que toque un modelo o una integración externa lleva
  al menos un test (`python manage.py test`) que cubra el camino feliz y el
  rechazo por permisos.
- Un test de permisos explícito por endpoint sensible: usuario sin el rol
  requerido debe recibir 403, no 500 ni un "funciona por accidente".
- `python manage.py check` antes de cerrar cualquier tarea, incluso sin CI
  configurado todavía.

## 11. Flujo de cambios (Git / PR)

1. Confirmar rama, working tree limpio y que no hay trabajo concurrente sin
   commitear antes de empezar.
2. Trabajar fuera de `main`; commits pequeños y reversibles.
3. Cerrar la tarea con: desarrollo → `python manage.py check` /
   `manage.py test` si aplica → revisión de que no quedaron credenciales ni
   URLs sensibles fuera de `config/constants.py`. Un build sin errores no
   sustituye probar el flujo real.
4. PR con descripción de qué cambia y por qué (no solo qué archivos toca),
   **incluyendo el contrato de cualquier endpoint nuevo o modificado**
   (§12) para que quien trabaje en `pamo_app_front` pueda consumirlo sin
   leer el código Python, y aprobación humana antes de merge.
5. Migraciones: revisar compatibilidad hacia atrás, aplicar `makemigrations`
   y confirmar el diff generado antes de commitear.
6. Ningún secreto en código, commits, logs ni descripciones de PR.

## 12. Contrato con el frontend

Backend y frontend son repos separados: la IA que trabaja en
`pamo_app_front` no puede leer este código para inferir qué existe. Todo lo
que el frontend necesita saber se documenta explícitamente:

- **Todo endpoint nuevo o cambiado** se describe con: método + path,
  permisos requeridos (`allowed_roles` o "público"), forma del body de
  entrada, forma de la respuesta (éxito y error), y códigos de estado
  posibles. Va en la descripción del PR como mínimo; si el catálogo de
  endpoints crece, vale la pena centralizarlo en un archivo propio
  (`docs/API-CONTRATO.md`) en vez de repetirlo en cada PR.
- **CORS**: `django-cors-headers` declara explícitamente el/los dominios
  del frontend en `CORS_ALLOWED_ORIGINS` — nunca
  `CORS_ALLOW_ALL_ORIGINS = True` en producción.
- **Sesión entre dominios distintos**: la autenticación es por cookie de
  sesión de Django (no JWT). Si frontend y backend terminan desplegados en
  dominios distintos (no solo subdominios del mismo dominio), la cookie
  necesita `SameSite=None; Secure` y el frontend debe mandar
  `withCredentials`/`credentials: include` en cada request — decisión a
  confirmar explícitamente en el momento del primer deploy real, no algo
  que se pueda dejar en el valor por defecto de Django sin revisar.
- **La tabla de áreas (§3.2)** es la misma en los dos repos — si se agrega
  un área nueva acá, se replica en `pamo_app_front/docs/GOVERNANCE.md`.

## 13. Checklist antes de cerrar cualquier tarea

- [ ] La funcionalidad quedó en el área correcta (§3) — si se creó una app
      nueva, la tabla del §3.2 quedó actualizada (y replicada en el repo de
      frontend).
- [ ] La lógica de negocio vive en `functions/`, no en la vista.
- [ ] Toda llamada a un proveedor externo pasa por `integrations/
      <proveedor>.py` — ninguna vista/función importó un SDK o hizo
      `requests` directo a un tercero.
- [ ] `python manage.py check` sin errores.
- [ ] Si se tocaron modelos: migración generada, revisada y commiteada.
- [ ] Todo `APIView` nuevo declara `permission_classes`/`allowed_roles`
      explícitos.
- [ ] Ninguna credencial fuera de `config/constants.py` / variables de
      entorno.
- [ ] Sin imports, vistas o rutas comentadas como código muerto.
- [ ] Cambios aditivos: ninguna funcionalidad existente quedó oculta o
      degradada sin que la tarea lo pidiera explícitamente.
- [ ] Si se creó o cambió un endpoint: el contrato (§12) quedó documentado
      para que el repo de frontend lo pueda consumir.
- [ ] Si se agregó un webhook (§4.3): vive en `webhooks.py` del área dueña
      del recurso, verifica firma vía `integrations/<proveedor>.py`,
      responde rápido y encola el procesamiento en Celery, y es idempotente.

## 14. Cómo evoluciona este documento

Esta guía se actualiza cuando se tome una decisión de arquitectura nueva o
cuando un patrón de aquí demuestre no funcionar en la práctica. Al cambiar
una regla, dejar registro de cuál era antes y por qué cambió — no
sobreescribir en silencio. Si el cambio afecta el contrato de API o la
tabla de áreas, replicarlo en `pamo_app_front/docs/GOVERNANCE.md` en el
mismo ciclo de trabajo.
