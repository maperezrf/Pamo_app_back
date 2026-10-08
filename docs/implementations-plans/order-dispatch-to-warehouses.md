# Plan: despacho de pedidos a bodegas con notificación por canal

Estado: **implementado sin activar (2026-10-07)**: fases 1 y 2 completas;
avisos de correo y WhatsApp construidos y apagados; aviso por API de Envía
bloqueado hasta el cruce SKU → `variantId`. Nada se ha enviado a ninguna
bodega. El proceso está registrado y sin programar. Detalle operativo en
[`../apps/orders.md`](../apps/orders.md) ("Despacho a bodegas"). Reutiliza
lo ya construido en
[`envia-fulfillment-dispatch.md`](envia-fulfillment-dispatch.md) (cliente de
Envía Fulfillment y descarga de guías de Mercado Libre y Falabella), que
pasa a ser una parte de este.

## Decisiones confirmadas por el usuario (2026-10-07)

1. **Ninguna tienda queda conectada a Envía** (ni Shopify, ni Mercado
   Libre, ni Falabella). Todo lo que se cree en Envía lo crea este backend.
2. **Este backend decide qué bodega despacha cada pedido** y le avisa a esa
   bodega por su canal. Envía es una bodega más: se le avisa por API.
3. **Bodegas: las ubicaciones de Shopify.** Cada una se parametriza con uno,
   varios o ningún canal de aviso: `api` (Envía), `email`, `whatsapp`.
4. **Vive en `orders`**: su objetivo es el flujo completo del pedido, desde
   la creación hasta el despacho y el tracking. No se crea `logistics`.
5. **Los pedidos de la tienda web entran al mismo flujo**: se les asigna
   bodega y se notifican igual que los de marketplace.

## Objetivo

Para cada pedido que llega a Shopify (marketplace o tienda web):

1. asignar **una** bodega de despacho;
2. obtener la guía cuando el canal la da;
3. avisar a la bodega por cada canal configurado (API de Envía, correo,
   WhatsApp), con los productos, la dirección y la guía;
4. dejar registrado qué se avisó, cuándo, por qué canal y con qué resultado,
   para verlo en el frontend y reintentar lo que falle.

El tracking de vuelta (estado del despacho hacia Shopify y los canales) es
una fase posterior.

## Bodegas actuales (Shopify, 2026-10-07)

| Ubicación | Id Shopify | Ciudad | Asignada por `orders` | Nota |
| --- | --- | --- | --- | --- |
| Bodega Envia | `97615380757` | Bogotá | 76 | Prioritaria (`FULFILLMENT_PRIORITY_LOCATION_ID`). Envía Fulfillment: bodegas Envía `311` "Bogotá CO" y `224` "Lastmile Bogotá". |
| Baru | `98784313621` | Itagüí | 23 | Itagüí es un nodo de origen de Mercado Libre. |
| Taumm | `99625828629` | Cota | 9 | Cota es un nodo de origen de Mercado Libre. |
| Boccherini | `99576545557` | Bogotá | 3 | Aviso por correo y WhatsApp (dicho por el usuario). |
| Proveedores | `94018535701` | Bogotá | 2 | |
| Bodega Compa | `98754560277` | Bogotá | 0 | |
| Amazon | `97972125973` | Bogotá | 0 | |

Los canales de cada una se configuran en el registro (ver "Diseño"); este
plan no los fija.

## Diseño

### 1. Registro de bodegas — `orders.DispatchLocation`

Una fila por ubicación de Shopify (sincronizada desde Shopify; no se
inventan bodegas):

- `shopify_location_id` (único), `name`, `is_active`;
- canales: `notify_api`, `notify_email`, `notify_whatsapp` (cada uno
  activable por separado; ninguno = el despacho queda para gestión manual);
- `emails` y `whatsapp_numbers` (listas), `envia_warehouse_id` (solo con
  `notify_api`), `requires_label` (no avisar hasta tener guía).

Se edita desde el admin de Django al principio y luego desde una pantalla
del frontend (contrato nuevo). Sin secretos: tokens y credenciales siguen en
`config/constants.py`.

### 2. Despacho — `orders.Dispatch` y `orders.DispatchNotification`

- **Un `Dispatch` por pedido de Shopify** (`ShopifyOrder`, la copia local):
  todos los pedidos, de marketplace o web, terminan ahí, así que es el ancla
  común. Guarda bodega, origen (canal), número de guía, `label_sha256` (el
  PDF no se guarda en la base), estado y error.
- Estados: `pending` → `waiting_label` → `ready` → `notified`, o `error` /
  `manual` / `cancelled`.
- **Un `DispatchNotification` por canal** (único por despacho y canal):
  estado, fecha, id externo (orden de Envía, mensaje de WhatsApp o correo)
  y error. Se reclama antes de enviar (`processing`), igual que
  `claim_orders`: un aviso nunca sale dos veces y un fallo de red queda para
  revisión.

### 3. Asignación de bodega

- **Marketplace**: la de hoy, `MarketplaceOrder.fulfillment_location_*`
  (elegida por `select_fulfillment_location` con el inventario de Shopify).
- **Tienda web**: nueva, con la misma regla
  (`select_fulfillment_location` sobre las líneas de `ShopifyOrderLine`, con
  `get_variant_inventory_by_sku`). Sin bodega que cubra el pedido →
  `novedad`, igual que en marketplace.
- **Abierta**: en Mercado Libre el canal ya elige un nodo de origen
  (Kennedy, Itagüí, Cota). La coincidencia de ciudades con Baru (Itagüí) y
  Taumm (Cota) sugiere que los nodos son bodegas de proveedores. Hay que
  decidir si manda el nodo de ML o nuestra regla, y mapear cada nodo a una
  ubicación de Shopify.

### 4. Guía

| Origen | Fuente de la guía | Estado |
| --- | --- | --- |
| Mercado Libre | `get_shipment_label` (`/shipment_labels`) | Hecho y probado en vivo. |
| Falabella | `get_shipping_document` (`GetDocument`) | Hecho y probado en vivo. |
| Tienda web | **Abierta**: el pedido no trae guía. Opciones: comprarla con la API de Shipping de Envía (`integrations/envia/`, credenciales pendientes) o que la bodega la genere. | Por decidir |
| Madecentro, Sodimac | Otro flujo ("diferente por el momento") | Fuera de alcance: el despacho queda `manual`. |

### 5. Notificadores

Una función por canal en `orders/functions/`, con la misma entrada (el
despacho con productos, dirección y guía) y la misma salida (id externo o
error):

- **`api` (Envía)**: `integrations.envia_fulfillment.create_order` con la
  guía en `shipments` y `envia_warehouse_id`. Necesita el `variantId` de
  Envía por SKU (pendiente, ver el subplan).
- **`whatsapp`**: `integrations.whatsapp` ya envía documentos y plantillas
  (`send_document_message`, `send_template_message`, `upload_document`).
  Mensajes iniciados por la empresa requieren **plantilla aprobada por
  Meta**.
- **`email`**: **no existe** en el backend. Hace falta elegir proveedor
  (SMTP de Google Workspace, Gmail API, un servicio transaccional) y crear
  `integrations/<proveedor>/`.

### 6. Proceso

`orders.dispatch_orders` en `orchestrator/registrations.py`
(`allow_concurrent=False`, programado cada pocos minutos): toma pedidos de
Shopify sin despacho o con despacho reintentable, asigna bodega, busca la
guía si la bodega la requiere y envía los avisos pendientes. Interruptores
en `config/constants.py`, apagados por defecto (incluido
`ENVIA_FULFILLMENT_WRITES_ENABLED`).

### 7. Frontend

- Pantalla de bodegas: canales y contactos de cada una.
- En el acordeón de pedidos: bodega, estado del despacho y de cada aviso,
  con reintento.

## Antes de desconectar las tiendas de Envía

Desconectarlas puede quitar cosas que hoy hace Envía por su cuenta. Hay que
confirmarlo con Envía y cubrirlo **antes**:

1. **Inventario**: si la conexión con Shopify sincroniza el stock de
   "Bodega Envia", al desconectarla nuestra asignación leería un inventario
   viejo. Alternativa: leer el inventario de Envía por su API.
2. **Pedidos web**: dejan de entrar solos a Envía; los crea este flujo.
3. **Guía de pedidos web**: hoy Envía la genera; ver la sección 4.
4. **Estado y tracking de vuelta** a Shopify y a los canales: hoy lo haría
   la conexión nativa; pasa a la fase de tracking.

## Fases

| Fase | Qué |
| --- | --- |
| 1 | `DispatchLocation` sincronizado desde Shopify y editable en el admin. Sin avisos. |
| 2 | `Dispatch` para pedidos nuevos (marketplace y web) con bodega y guía; visible en el listado. Sin avisos. |
| 3 | Notificador de WhatsApp (plantilla aprobada) y de correo (proveedor elegido). Piloto con Boccherini. |
| 4 | Notificador de Envía (`variantId`, sandbox, piloto de un pedido) y desconexión de las tiendas en Envía, una por una, tras resolver "Antes de desconectar". |
| 5 | Tracking de vuelta a Shopify y a los canales. |

## Decisiones abiertas

1. Nodo de Mercado Libre vs. nuestra regla de bodega, y el mapa de nodos a
   ubicaciones.
2. Guía de los pedidos de la tienda web.
3. Proveedor de correo.
4. Plantillas de WhatsApp (texto y aprobación en Meta).
5. Qué hace cada bodega con el aviso (¿confirma recepción? ¿cómo nos
   devuelve el número de guía o el estado?).

## Riesgos

- Avisar dos veces o a la bodega equivocada: reclamo por canal y bodega
  fija en el despacho una vez notificado.
- Datos personales del comprador en correos y WhatsApp a terceros: enviar
  solo lo necesario para despachar.
- Envía sin búsqueda confiable por identificador (ver el subplan): la
  idempotencia de Envía se apoya en `DispatchNotification`.

## Pruebas

Sin red (`NoNetworkTestRunner`): sincronización de bodegas; asignación web
y marketplace; despacho que espera guía; cada notificador (éxito, error,
reintento sin duplicar); bodega sin canales → `manual`; API de bodegas y
del estado del despacho con rechazo por permisos.

## Documentos a actualizar

`docs/architecture/APP_BOUNDARIES.md` (responsabilidad de `orders` ampliada
a despacho y tracking), `docs/apps/orders.md`, `docs/contracts/API.md`,
`docs/apps/orchestrator.md`, `docs/architecture/INTEGRATIONS.md` (correo) y
el expediente de pedidos del frontend.
