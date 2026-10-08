# Plan de prueba: una OC de Sodimac en Envía Fulfillment con su guía

Estado: **ejecutada con éxito (2026-10-08)**; pendiente lo de "Después de
la prueba" y reactivar el sync de Sodimac. Primera escritura real en Envía
Fulfillment. Parte del plan
[`envia-fulfillment-dispatch.md`](envia-fulfillment-dispatch.md), dentro de
[`order-dispatch-to-warehouses.md`](order-dispatch-to-warehouses.md). Al
cerrar lo pendiente, lo vigente pasa a esos planes o a `docs/apps/` y este
documento se borra.

## Resultado (2026-10-08)

| OC | Orden Envía | Producto (`variantId`) | Bodega | Guía (pre-tracking) | Remesa (comentario) |
| --- | --- | --- | --- | --- | --- |
| 16198691 | `49684722` `sodimac-16198691` | `5076` (213962) x1 | 311, `PENDING` | TCC 474231319, PDF guardado | PDF adjunto |
| 16200704 | `49684814` `sodimac-16200704` | `PAMO_P04` (215359) x1 | 311, `PENDING` | TCC 474231303, PDF guardado | PDF adjunto |

Sin alertas ni causas de "no enviado a bodega". **Operaciones confirmó
(2026-10-08) que quedaron bien creadas.** Única observación: en el panel
quieren ver solo el número (`16200704`), no `sodimac-16200704`. Las 2 de
prueba no se pueden renombrar: `PUT /company/{companyId}/client/order/{id}`
exige `currency` y responde **409 "The order cannot be edited in its current
status"** con la orden en bodega (`PENDING`); no se cambió nada. El formato
quedó corregido para las siguientes (`_envia_identifier`).

Aprendido (ya reflejado en `integrations/envia_fulfillment/`):

- `create_order` exige `shopId` (tienda principal `1631`), apellido no vacío
  y `state.code` no vacío, aunque la colección no lo diga. El código de
  departamento es `code_2_digits` de
  `https://queries.envia.com/state?country_code=CO` (Cundinamarca `CN`,
  Magdalena `MA`, Bogotá `DC`, Antioquia `AN`, Santander `SN`); el token de
  Fulfillment sirve para esa consulta. Sin código postal se acepta.
- La guía enviada en `shipments` al crear queda como **pre-tracking**
  (`pretrackings` del listado: número, `label` en S3); `shipments` queda
  vacío hasta el despacho. El data URL (`data:application/pdf;base64,`) se
  acepta.
- La remesa va como comentario con archivo; Envía guarda el PDF y lo deja
  como un segundo comentario con la URL.
- SKU → `variantId`: `GET /company/{companyId}/warehouse/inventory`
  (paginado con `page`/`limit`, 1.009 productos) trae `variantId`, `sku`,
  stock por bodega y, por tienda, el `ecartId`, que es el **id de la
  variante en Shopify**. El SKU es el mismo de Shopify.
- Sodimac trae el nombre en un solo campo (`CLIENTE`): primera palabra como
  nombre, resto como apellido.

## Objetivo

Comprobar, con **una sola OC real**, que este backend puede crear una orden
en Envía Fulfillment con la guía de despacho adjunta, y que la bodega de
Envía la recibe y la puede despachar. Sodimac sirve porque llegan OC todos
los días y su reporte trae todos los datos de entrega.

## Estado al preparar la prueba (2026-10-07)

- **Sync de Sodimac pausado** en producción: las 4 programaciones de
  `orders.sync_sodimac` (`ProcessScheduleConfig` ids 15, 16, 17 y 19; cron
  `0 7`, `0 5`, `0 13` y `0 10`) quedaron con `is_active=False`. Así la OC
  de prueba no se crea en Shopify y no llega duplicada a Envía por la tienda
  de Shopify conectada. **Mientras siga pausado, ninguna OC de Sodimac se
  crea en Shopify.**
- `invoicing.invoice_sodimac` (id 18) sigue activo: no lee la cola.
- Se descargó la cola (11 OC, tipo `1`; tipo `4` vacío) y **las 11 se
  reinyectaron**: siguen en la cola. El JSON se guardó fuera del repositorio
  (trae datos personales del comprador).
- El reporte de Sodimac trae todo lo que Envía pide: `CLIENTE`,
  `DIRECCION_ENTREGA`, `CIUDAD_ENTREGA`, `DEPARTAMENTO_ENTREGA`,
  `TELEFONO_CLIENTE`, `EMAIL_CLIENTE`, `CEDULA`, y la transportadora
  (`TRANSPORTADORA`, ej. `5-TCC`, `32-HOMEDELIVERY`). Hoy
  `orders.sync_sodimac` no guarda esos campos.

### OC candidatas (transmitidas el 07/10, `1-PENDIENTE`)

Cobertura de Bodega Envia según el stock de Shopify (`get_variant_inventory_by_sku`)
el 2026-10-07:

| OC | SKU Sodimac → Pamo | Stock Bodega Envia | Nota |
| --- | --- | --- | --- |
| **16200704** | 390335 → `PAMO_P04` x1 | 35 | Recomendada (un producto). TCC. |
| **16198691** | 726410 → `5076` x1 | 46 | Recomendada (un producto). TCC. |
| 16198449 | 473293 → `PAMO_BID180` x2 | 142 | TCC. |
| 16198440 | 3070702 → `BYJ-114AB` x1 | 1 | Stock justo. TCC. |
| 16198435 | 468907 → `PAMO_6615A` x6 | 31 | HomeDelivery. |
| 16200454 | kit → 4 componentes | cubre | Segunda prueba (kit). HomeDelivery. |
| 16200841 | kit → 6 componentes | cubre | Segunda prueba (kit). TCC. |
| 16201014 | 3098646 → `LT7045` x1 | 0 | No sirve: sin stock en Envía. |

16170356, 16168387 y 16166388 (29–30/09) son de `pamo_web` (antes del
corte) y no aplican.

## Pasos

1. **Operaciones** entrega dos PDF por OC, el **rótulo** (guía) y la
   **relación** (remesa), y el número de guía, para **una** OC
   (recomendadas: 16200704 o 16198691).
2. **Datos frescos**: volver a descargar la cola (tipo `1` y `4`),
   guardar el JSON fuera del repositorio apenas llega y **reinyectar cada OC
   leída** (`reinject_order`). Comprobar que la OC elegida sigue
   `1-PENDIENTE`.
3. **Verificación en Envía, solo lectura**
   (`integrations/envia_fulfillment/`, empresa `2156`, tienda principal
   `1631`):
   - `variantId` de Envía de cada SKU de Pamo en la tienda principal. Fuente
     probada: los ítems de las órdenes que Envía importó de Shopify traen
     `variantId` + `sku` (`get_order`). El catálogo completo
     (`list_shop_products`) pasa de 4 MB y no sirve sin paginar.
   - Stock de ese `variantId` en la bodega `311` "Bogotá CO "
     (`GET /company/{companyId}/warehouse/inventory/product/{variantId}`).
   - Que no exista ya una orden con el identificador de prueba
     (`find_order` hoy falla con `ENVIA_FULFILLMENT_SEARCH_UNRELIABLE`:
     revisar a mano en el panel de Envía).
4. **Crear la orden, una sola vez**, con
   `integrations.envia_fulfillment.create_order`:
   - `identifier="sodimac-<OC>"` (así quedaron las 2 de prueba; desde el
     2026-10-08 el formato es solo `<OC>`, a pedido de operaciones),
     `warehouse_id=311`, `ecommerce_status="paid"`,
     `currency="COP"`;
   - `products` con el `variantId` del paso 3, la cantidad y el precio
     (`COSTO_SKU` sin IVA);
   - `shipping_address` y `email` desde el JSON del paso 2;
   - `tracking_number` y `label_pdf` con el **rótulo** (la guía) de
     operaciones.
   - Después, la **relación (remesa)**, el segundo PDF de la transportadora,
     con `add_order_comment(orderId, comment="Remesa", pdf=...)`: la API no
     tiene otro lugar para un documento aparte de la guía. Operaciones dice
     que en el panel va en un campo de mensaje "remesa"; confirmar que es
     ese comentario (`GET …/client/order/{orderId}/comments` de una orden
     donde ellos la hayan cargado).
   - `ENVIA_FULFILLMENT_WRITES_ENABLED=True` **solo como variable de esa
     ejecución**, nunca en `.env` ni en Railway.
5. **Verificar en el panel de Envía**: la orden aparece con la bodega
   correcta, el producto, la dirección y la guía descargable; anotar el
   `orderId` y el `warehouseStatus`. Con `get_order` revisar `shipments` y
   compararlo con una orden que creó la integración nativa de Mercado Libre.
6. **Reactivar el sync de Sodimac** (`is_active=True` en los ids 15, 16, 17
   y 19) en cuanto termine la prueba. Antes, decidir qué pasa con la OC de
   prueba (ver "Después de la prueba").

## Criterios de éxito

- Envía responde `check: true` con `orderId`.
- En el panel, la orden queda en la bodega `311` con la guía PDF adjunta y
  la misma forma que las órdenes nativas.
- La bodega de Envía confirma que la puede despachar con esa guía.

## Riesgos y cuidados

- **Duplicado en Envía**: si la OC de prueba llega a Shopify, Envía la
  importa por la tienda de Shopify conectada. Por eso el sync sigue pausado
  hasta decidir.
- **Retraso de despachos**: con el sync pausado, ninguna OC de Sodimac se
  crea en Shopify. No dejarlo pausado más de lo necesario.
- **Formato de la guía** (`label.raw`): la colección oficial dice base64,
  pero su ejemplo manda un data URL; se usa el data URL. Si Envía rechaza o
  la guía no se ve, probar con base64 puro.
- **Datos personales**: el JSON de Sodimac y la guía no se guardan en el
  repositorio.

## Después de la prueba

- **Las 2 OC de prueba ya están en la base** (las guardó una corrida manual
  de `orders.sync_sodimac` sin crearlas en Shopify, junto con otras 9).
  Antes de reactivar el sync hay que excluirlas, o el sync las crea en
  Shopify y Envía las importa duplicadas por la tienda conectada. Propuesta:
  pasarlas a `procesando` en el admin (el sync solo crea `pending` /
  `error_creando_orden`; la reinyección y la facturación siguen igual).

- Decidir el destino de la OC de prueba: seguir en Envía y no crearla en
  Shopify (agregarla a `SODIMAC_LEGACY_OCS` mientras tanto), o crearla en
  Shopify y cancelar la copia que importe Envía.
- Facturación: la OC se factura cuando llegue a `4-ESTADO FINAL` solo si
  existe su `MarketplaceOrder`; si se excluye del sync, no se facturará sola.
- Si sale bien, siguiente paso: guardar los datos de entrega de Sodimac en
  `orders.sync_sodimac` y conectar Sodimac al flujo de despacho a bodegas
  (hoy Sodimac queda `manual` en `assign_dispatch_location`).
- Registrar el resultado en
  [`envia-fulfillment-dispatch.md`](envia-fulfillment-dispatch.md) ("Avance")
  y borrar este documento.
