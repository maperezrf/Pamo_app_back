# App `invoicing`

`invoicing/` es dueña de las facturas en Siigo de los pedidos de marketplace
y de las reglas para calcularlas. Hoy solo factura Sodimac. Depende de
`orders` (lee `MarketplaceOrder` y sus ítems), de `products` (equivalencias
y reparto de kits) y de `integrations.siigo` (transporte). `orders` no
importa `invoicing`. Plan:
[`orders.md`](orders.md) ("Sodimac").

## Modelo `Invoice`

- **Una factura por pedido**: `OneToOneField` a `MarketplaceOrder`
  (`related_name="invoice"`, `PROTECT`). La base de datos impide facturar
  dos veces.
- Las líneas quedan en `request_payload` (los argumentos enviados a
  `integrations.siigo.create_invoice`), sin tabla propia. La respuesta de
  Siigo queda en `response_payload`.
- Guarda los totales calculados (`subtotal`, `iva`, `reteiva`, `reteica`,
  `retefuente`, `total` = valor del pago), si se timbró (`stamped`) y lo que
  devolvió Siigo (`siigo_id`, `number` = `name`, `siigo_total`).

| Estado | Significado |
| --- | --- |
| `creando` | Se escribe **antes** de llamar a Siigo. Si queda así, el proceso murió o la red falló y no se sabe si la factura existe. **No se reintenta sola**: revisar en Siigo; si no existe, pasarla a `error` desde el admin para que la siguiente corrida la reintente. `error_message` tiene el detalle. |
| `creada` | Éxito. |
| `error` | Siigo rechazó la factura (`SiigoAPIError`): no existe. La siguiente corrida la reintenta. Si el mensaje es *"The total payments must be equal to the total invoice. The total invoice calculated is X"*, X queda en `siigo_calculated_total` y el reintento lo envía como pago (igual que `pamo_web`). |

El reintento desde `error` y la creación nueva escriben `creando` de forma
condicionada (`update` filtrado por `status=error`, o `create` protegido por
el `OneToOne`). Así dos corridas no facturan la misma OC.

## Proceso `invoicing.invoice_sodimac`

`invoicing/functions/invoice_sodimac_orders.py`, `allow_concurrent=False`,
programado por la API del orquestador:

- Toma las OC de Sodimac con `marketplace_status = "4-ESTADO FINAL"` sin
  factura o con factura en `error`.
- **Factura aunque la orden de Shopify haya fallado**, como `pamo_web`: la
  factura sale de los ítems guardados, no de Shopify.
- `params={"limit": N}` para una corrida controlada.
- Si alguna OC falla, la ejecución termina en error con el resumen.
- Es independiente de `orders.sync_sodimac`: no lee la cola de Sodimac, solo
  la base de datos. Se programa después de la sincronización.

## Cálculo (`invoicing/functions/build_sodimac_invoice.py`)

Reglas de `pamo_web` (`SigoConnection.get_data` / `create_invoice`):

- **Líneas**: cada ítem (precio = `COSTO_SKU` sin IVA) se traduce con el
  catálogo. Sin equivalencia, el SKU va tal cual. Producto simple, con su
  SKU de Pamo. Kit, una línea por componente con el precio repartido
  (`products.expand_product`). Cada línea lleva los impuestos
  `16104` y `13456` y descuento 0.
- **Totales**:
  - `iva = subtotal × 0.19`, `reteiva = iva × 0.15`,
    `reteica = subtotal × 0.01104`, `retefuente = subtotal × 0.025`;
  - `total = subtotal + iva − reteiva − reteica − retefuente`.
  - Cada término se redondea a 2 decimales (half-up, con `Decimal`).
  - Diferencia deliberada con `pamo_web`: el subtotal se suma sobre los
    precios **ya redondeados** de las líneas, que son los que suma Siigo.
    `pamo_web` usaba el costo sin redondear, y por eso a veces Siigo
    rechazaba el pago. El reintento con `siigo_calculated_total` sigue
    cubriendo cualquier diferencia restante.
- **Datos fijos** en `invoicing/constants.py` (valores de negocio, no
  secretos): documento `26647`, centro de costo `116`, vendedor `643`,
  retenciones `[13457, 13464]`, pago `6507`, cliente SODIMAC COLOMBIA S A
  (NIT `800242106`) con la dirección y los contactos de `pamo_web`.
  `purchase_order_number` = OC.
- **Timbrado**: `SODIMAC_STAMP_SEND = True` y `SODIMAC_MAIL_SEND = True`,
  como en `pamo_web` (activado el 2026-10-07; antes `False` mientras se
  probaba). Cada factura se timbra ante la DIAN y se envía por correo; un
  timbrado no se revierte. Para volver a facturar sin timbrar, las dos en
  `False`.
- La base del subtotal (precios de línea ya redondeados) se mantiene a
  propósito aunque difiera de `pamo_web` (confirmado por el usuario el
  2026-10-07).

## Incidente 2026-10-05 a 2026-10-07: facturas en `creando`

Las 5 OC que llegaron a estado final (16176375, 16182568, 16182869,
16184959, 16186321) quedaron en `creando` con
`DataError: value too long for type character varying(512)`. Causa:
`SiigoToken.token` era `CharField(512)` y el JWT de Siigo es más largo; el
guardado del token fallaba en `SiigoClient._valid_token()`, que corre al
armar los headers, **antes** de enviar la factura. Ninguna llegó a Siigo,
aunque el proceso las marcó "resultado incierto" (regla general ante una
excepción). Corregido con `token` como `TextField`
(`integrations/migrations/0003_siigo_token_text.py`, desplegado el
2026-10-07). Ese mismo día, ya desplegado, las 5 filas se pasaron de
`creando` a `error` para que la siguiente corrida las facture (ya con
timbrado ante la DIAN).

## Visibilidad

`Invoice` está en el admin de Django; lo que devolvió Siigo es de solo
lectura. Todavía no hay API para el frontend.

## Pruebas

`invoicing/tests.py`, con Siigo simulado: totales de `pamo_web` para una OC
simple, kit con precio repartido, SKU sin equivalencia, total calculado por
Siigo; proceso con éxito (datos fijos y sin timbrar), factura aunque
Shopify haya fallado, no factura OC abiertas, de otro canal ni ya
facturadas, rechazo → `error` y reintento, rechazo con total calculado →
reintento con ese total, fallo incierto → queda `creando` y no se
reintenta, `limit`, `ProcessType` sembrado.

```
DATABASE_URL= python manage.py test invoicing
```
