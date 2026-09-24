# Patrón: webhooks entrantes de un proveedor externo

Un webhook es "una API que espera a que la llamen" — el proveedor externo
(Shopify, WhatsApp, GitHub) hace la petición, no nosotros. Vive separado de
las APIs internas de la app, en su propio archivo.

## Reglas

- El endpoint vive en `<app>/webhooks.py`, no en `views.py`/`apis.py`. Así
  queda claro a simple vista qué endpoints los llama un tercero (sin
  sesión, sin API key interna) y cuáles consume el frontend o un cliente
  interno.
- Permiso: `AllowAny` explícito. El webhook no tiene sesión de Django ni
  `X-API-Key` — `ApiKeyRequiredMixin` es solo para consumidores internos
  máquina-a-máquina, no para webhooks de proveedor (ver
  `docs/patterns/AUTHORIZATION.md`).
- La única autenticación real es la **firma** que manda el proveedor.
  Verificarla siempre contra `request.body` (bytes crudos), **antes** de
  tocar `request.data` — la firma se calculó sobre los bytes exactos que
  mandó el proveedor, no sobre una re-serialización de DRF, y acceder a
  `request.data` primero puede consumir el stream.
- La función que verifica la firma vive en `integrations/<provider>/client.py`
  (ej. `WhatsAppClient.verify_signature`, o `ShopifyClient.verify_webhook_signature`
  para el caso nuevo de Shopify) — no se reimplementa por endpoint.
- El endpoint responde rápido: no procesa nada pesado dentro del request.
  Encola el trabajo real vía `orchestrator.services.launch_process(...)`
  (ver `docs/apps/orchestrator.md`) y responde `200` de inmediato —
  `launch_process` es de por sí no bloqueante (crea el `ProcessExecution` y
  arranca un hilo en segundo plano). Esto actualiza el patrón viejo que se
  veía en `feature_tracking/webhooks.py` (ya eliminado), que procesaba todo
  síncrono porque `orchestrator` no existía todavía en ese momento.
- El `ProcessType` de un proceso disparado por webhook casi siempre necesita
  `allow_concurrent=True`: pueden llegar varios eventos del proveedor casi
  al mismo tiempo, y el rechazo por defecto del orquestador (409 si ya hay
  uno corriendo) está pensado para "no lances dos veces el mismo reporte",
  no para eventos independientes concurrentes. Con `allow_concurrent=False`
  se pierden webhooks.
- El registro de la llamada y el seguimiento del proceso **no necesitan un
  modelo nuevo**: cada `launch_process()` ya crea un `ProcessExecution` con
  estado, progreso, error y timestamps — el mismo mecanismo que cualquier
  otro proceso del orquestador. No duplicar esa capacidad con un log propio
  de la app.

## Excepción: proveedor que no firma (Mercado Libre)

Mercado Libre no firma sus notificaciones. Cuando no hay firma que
verificar, el webhook debe cumplir tres condiciones en su lugar (caso
aplicado: `orders/webhooks.py`, `MercadoLibreOrderWebhookView`):

1. **El payload nunca se usa como dato.** Solo se extrae el identificador
   del recurso y el dato real se lee de la API del proveedor con el token
   propio. Un payload falso, a lo sumo, provoca una lectura.
2. **Validar todo lo que el payload permite**: tópico, formato del
   recurso, id de la app propia y id de la cuenta conectada.
3. **Responder `200` también a lo inválido**, sin lanzar proceso: no
   provocar reintentos ni dar pistas sobre qué se valida.

El proceso lanzado debe ser idempotente: estos proveedores suelen mandar
una notificación por cada cambio del recurso.

## Estructura de archivos resultante

En la app dueña de los datos que trae el webhook:

- `<app>/webhooks.py`: el/los `APIView`. Verifica la firma, llama
  `launch_process`, responde. No parsea el payload de negocio ni persiste
  nada — eso es del proceso registrado.
- `<app>/functions/parse_<evento>_webhook.py`: normaliza el payload crudo
  del proveedor a un dict propio de la app (mismo criterio que
  `integrations/whatsapp/functions/parse_webhook_event.py`).
- `<app>/functions/process_<evento>_webhook.py`: el callable registrado en
  el orquestador (`(params, progress_callback=None, cancellation_token=None)`).
  Llama al parser y después a la función que persiste.
- Una función de persistencia (ej. `upsert_...py`) reutilizada tanto por el
  webhook como por cualquier reconciliación/backfill periódica del mismo
  dato — para no duplicar "cómo se guarda un registro" en dos lugares.

## Ejemplo (Shopify → app `customers`)

```
customers/webhooks.py                          -- ShopifyCustomerWebhookView
customers/functions/parse_customer_webhook.py   -- payload crudo -> dict propio
customers/functions/process_customer_webhook.py -- callable registrado
customers/functions/upsert_from_shopify_customer.py -- persistencia (también la usa la reconciliación)
```

Ver `docs/implementations-plans/shopify-customers-directory.md` para el caso
completo aplicado.
