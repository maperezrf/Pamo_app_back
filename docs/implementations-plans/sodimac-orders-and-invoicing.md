# Plan: pedidos de Sodimac y facturación en Siigo

Estado: **implementado en este repositorio (2026-10-01)**; cambios de
`pamo_web` en la rama `sodimac-invoices-only` de ese repo, sin desplegar.
El corte (procedimiento abajo) no se ha hecho. Es el paso 2 de la migración
de Sodimac desde `pamo_web`. El paso 1 (catálogo) está en
[`products-catalog.md`](products-catalog.md). El paso 3 es el corte por
convivencia, sin migrar datos de `SodimacOrders`: ver
[Parte C](#parte-c-corte-por-convivencia-con-pamo_web).

Decisiones confirmadas por el usuario el 2026-09-25:

- El precio de un kit se reparte **en partes iguales** por unidad de
  componente, como en `pamo_web`.
- Al principio se factura **sin timbrar ante la DIAN**
  (`stamp_send=False`), para hacer pruebas.
- La factura se guarda en una app nueva **`invoicing`**, general y no solo
  para Sodimac. Las líneas de la factura quedan en el JSON enviado, sin
  tabla propia.

## Cómo funciona Sodimac (resumen de `pamo_web`)

- **Cola destructiva**: el reporte (`TipoOrden` `1` y `4`) devuelve las OC
  que hay en cola y las saca de ella. Si una OC no se guarda en el momento
  de leerla, se pierde.
- **Reinyección**: `reinject_order(oc)` devuelve una OC a la cola; así
  aparece en la siguiente lectura con su estado actual. Es la única forma
  de consultar el estado de una OC.
- **Estados** (`ESTADO_OC`) vistos en `pamo_web`: `1-PENDIENTE`,
  `3-EN TRANSPORTE`, `4-ESTADO FINAL`. En estado final se factura.
- En `pamo_web` la orden de Shopify se crea con `financial_status: pending`,
  a nombre del cliente fijo de Sodimac, con tag `SODIMAC`. El precio es el
  `COSTO_SKU` **sin IVA**.

## Reparto del precio de kits (todos los canales)

Hoy `process_shipment` deja el pedido en `error_creando_orden` si el SKU es
un kit. Se reemplaza por la regla de `pamo_web`:

- Por `n` unidades de un kit con precio unitario `P` y componentes
  `{c_i: q_i}`, cada componente va con cantidad `n × q_i` y precio
  unitario `P / Σ q_i`.
- El total de la orden se conserva: `Σ (n × q_i) × P / Σ q_i = n × P`,
  salvo el redondeo a 2 decimales por línea.
- `expand_product(product, quantity, unit_price=None)` en `products` agrega
  `price` a cada línea cuando recibe `unit_price`. La regla queda en un
  solo lugar y la usan `process_shipment` y la factura.
- En `process_shipment`, `_resolve_line_items` expande el ítem en una línea
  por componente, y consulta cada componente en Shopify (variant y stock
  por bodega).

## Parte A: `orders.sync_sodimac` (app `orders`)

### Modelo

Una OC de Sodimac es un `MarketplaceOrder` con `marketplace="sodimac"` y
`marketplace_order_id = OC`. Se agregan dos campos genéricos (vacíos para
los otros canales):

| Campo | Uso |
| --- | --- |
| `marketplace_status` | Último estado reportado por el marketplace (`ESTADO_OC`). |
| `marketplace_created_at` | Fecha de transmisión de la OC (`FECHA_TRANSMISION`). |

Los ítems (`MarketplaceOrderItem`) guardan `marketplace_sku`, `quantity` y
`unit_price` = `COSTO_SKU` sin IVA. Son la fuente de la factura: no se
vuelve a leer el detalle desde Sodimac.

### Proceso (`orders/functions/sync_sodimac_orders.py`)

1. **Reinyectar** las OC abiertas: `marketplace_status` distinto de
   `4-ESTADO FINAL`. Un fallo de reinyección de una OC se informa y no
   detiene el resto.
2. **Leer la cola** (`get_orders("1")` y `get_orders("4")`), agrupar filas
   por OC y **guardar de inmediato** cada OC: crear la fila y sus ítems si
   es nueva, y actualizar siempre `marketplace_status`.
3. **Crear en Shopify** las OC sin orden (`shopify_order_id == ""`, estado
   reintentable), con reclamo atómico (`claim_orders`) y
   `process_shipment([order], SODIMAC_SHOPIFY_CUSTOMER_ID)`. Un pedido = un
   envío.
4. Resumen al orquestador. Si hubo fallos, la ejecución termina en error,
   igual que Madecentro.

`params` opcional: `{"limit": N}` limita cuántas OC se crean en Shopify en
esa corrida (para la primera corrida controlada). No limita la lectura de
la cola, porque lo leído se perdería si no se guarda.

### Configuración

- `SODIMAC_SHOPIFY_CUSTOMER_ID` en `config/constants.py` (en `pamo_web`:
  `7247084421397`).
- `ProcessType` `orders.sync_sodimac` por migración de datos
  (`allow_concurrent=False`) y `register_process` en
  `orchestrator/registrations.py`.

## Parte B: `invoicing.invoice_sodimac` (app nueva `invoicing`)

### Límite

`invoicing` es dueña de las facturas y de las reglas para calcularlas.
Depende de `orders` (lee `MarketplaceOrder`), de `products` (reparto de
kits) y de `integrations.siigo`. `orders` no importa `invoicing`. Se
actualiza `APP_BOUNDARIES.md`: el área "Facturación" pasa de `facturacion/`
a `invoicing/`.

### Modelo

```python
class Invoice(models.Model):
    class Status(models.TextChoices):
        CREATING = "creando"       # se llamó a Siigo; si el proceso muere acá, revisión manual
        CREATED = "creada"
        ERROR = "error"            # Siigo rechazó: se reintenta en la siguiente corrida

    marketplace_order = models.OneToOneField(MarketplaceOrder, on_delete=models.PROTECT, related_name="invoice")
    status = models.CharField(max_length=20, choices=Status.choices)
    customer_identification = models.CharField(max_length=32)
    customer_name = models.CharField(max_length=255)
    purchase_order_number = models.CharField(max_length=64, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2)
    reteiva = models.DecimalField(max_digits=14, decimal_places=2)
    reteica = models.DecimalField(max_digits=14, decimal_places=2)
    retefuente = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)          # valor del pago enviado
    stamped = models.BooleanField()                                       # stamp_send enviado
    siigo_id = models.CharField(max_length=64, blank=True)
    number = models.CharField(max_length=32, blank=True)                  # ej. FV-1-1234
    siigo_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    siigo_calculated_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    invoice_date = models.DateField()
    error_message = models.TextField(blank=True)
    request_payload = models.JSONField(default=dict)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

- **Una factura por orden**: `OneToOneField`, así la base de datos impide
  facturar dos veces.
- **`creando`**: el estado se escribe **antes** de llamar a Siigo. Si el
  proceso muere o la red falla, no se sabe si la factura quedó creada. No
  se reintenta sola; se revisa en Siigo a mano (mismo criterio que
  `procesando` en Mercado Libre).
- **`error`**: Siigo respondió con un rechazo (`SiigoAPIError`), así que la
  factura no existe. Se reintenta en la siguiente corrida.
- **`siigo_calculated_total`**: si Siigo rechaza con *"The total payments
  must be equal to the total invoice. The total invoice calculated is X"*,
  se guarda X y el reintento envía X como pago. Es el comportamiento de
  `pamo_web`.

### Cálculo (`invoicing/functions/build_sodimac_invoice.py`)

Copia exacta de `pamo_web` (`SigoConnection.get_data` / `create_invoice`):

- **Ítems**: cada `MarketplaceOrderItem` se traduce con el catálogo y se
  expande con `expand_product(..., unit_price)`. Cada línea es
  `{"code": sku_pamo, "quantity", "price": round(p, 2), "discount": 0, "taxes": [{"id": 16104}, {"id": 13456}]}`.
  Un SKU sin equivalencia va tal cual, como en `process_shipment`.
- **Totales**:
  - `subtotal = Σ price × quantity`
  - `iva = subtotal × 0.19`
  - `reteiva = iva × 0.15`
  - `reteica = round(subtotal × 0.01104, 2)`
  - `retefuente = subtotal × 0.025`
  - `total = round(subtotal + round(iva, 2) - round(reteiva, 2) - round(reteica, 2) - round(retefuente, 2), 2)`
  - Si hay `siigo_calculated_total` de un rechazo anterior, `total` es ese
    valor.
- **Datos fijos**:
  - documento `26647`, centro de costo `116`, vendedor `643`;
  - retenciones `[13457, 13464]`, pago `6507`;
  - cliente SODIMAC COLOMBIA S A, NIT `800242106`, con la dirección y los
    contactos de `pamo_web`;
  - `purchase_order_number` = OC.

  Viven en `invoicing/constants.py`: son valores de negocio, no secretos.
- `stamp_send=False` y `mail_send=False` mientras se prueba. Pasar a timbrar
  es un cambio de constante, documentado.

### Proceso (`invoicing/functions/invoice_sodimac_orders.py`)

1. Selecciona OC de Sodimac con `marketplace_status = 4-ESTADO FINAL` y sin
   factura, o con factura en `error`.
2. Por cada una: calcula, guarda `Invoice(status=creando, request_payload=...)`
   y llama a `integrations.siigo.create_invoice`.
   - Éxito: `creada`, con `siigo_id`, `number` (`name` en Siigo),
     `siigo_total`.
   - `SiigoAPIError`: `error` con el mensaje; si es el de total calculado,
     guarda `siigo_calculated_total`.
   - Cualquier otra excepción: queda en `creando` y se informa.
3. `params`: `{"limit": N}` para la primera corrida controlada.

### Visibilidad

`Invoice` en Django admin (solo lectura de los campos de Siigo). Una API
para el frontend queda para cuando la pida el front.

## Parte C: corte por convivencia con `pamo_web`

Decidido con el usuario el 2026-10-01. No se migran datos: cada sistema
factura sus propias OC. Las OC nuevas viven aquí desde el corte. Las OC
antiguas sin factura terminan su ciclo en `pamo_web`, que solo factura.

### Por qué hay que encadenar

`handle_invoices_and_billing` de `pamo_web` no solo factura. Antes reinyecta
sus OC en `1-PENDIENTE` (desde el 2026-04-01) y **lee la cola** (tipos `1`
y `4`) para conocer el estado. Así que dos sistemas leen la misma cola
destructiva. Reglas:

1. **Nunca al mismo tiempo.** Al terminar `orders.sync_sodimac`, se llama
   de inmediato al endpoint de `pamo_web`. Ese endpoint es síncrono: corre
   el proceso entero dentro de la solicitud.
2. **`pamo_web` devuelve lo que no conoce.** Si entre las dos lecturas llega
   una OC nueva, la lee `pamo_web`. Ojo: `_save_order` de `pamo_web` hace
   `get_or_create` de **toda** OC leída, así que "desconocida" se decide
   con los IDs de `SodimacOrders` tomados **antes** de leer. Esas OC se
   reinyectan una por una, se borra la fila recién creada y se sacan del
   lote antes de facturar. Las que no se pudieron reinyectar vuelven en
   `not_returned` y aquí se reportan como fallo.
3. **Aquí se ignoran las OC antiguas.** Si una OC tiene `FECHA_TRANSMISION`
   anterior a `SODIMAC_CUTOVER_DATE`, o está en `SODIMAC_LEGACY_OCS`, aquí
   no se guarda ni se crea en Shopify: se reinyecta para que la lea
   `pamo_web` justo después. Así una OC de `pamo_web` que aparezca en la
   cola no se duplica en Shopify ni en Siigo, y tampoco se pierde.

### Flujo

1. Aquí: `orders.sync_sodimac` (reinyecta, lee, guarda, crea en Shopify).
2. Al terminar, el mismo proceso llama a
   `POST /pamo_bots/sodimac/invoices` de `pamo_web`. Si la llamada falla,
   se informa en el resumen y no revierte lo de aquí.
3. `pamo_web`: reinyecta sus OC, lee la cola, devuelve las OC
   desconocidas y factura las que estén en estado final.
4. Aquí, por separado: `invoicing.invoice_sodimac` (solo base de datos, no
   lee la cola). Al principio no factura nada, porque ninguna OC nueva está
   en estado final.

### Cambios en `pamo_web`

Rama `sodimac-invoices-only` de `pamo_web` (desde `origin/main`
`6f35cd8`), sin publicar:

- `POST /pamo_bots/sodimac/invoices` (`sodimac_invoices`, `@csrf_exempt`,
  `@require_POST`, `@require_bot_token`): llama a
  `handle_invoices_and_billing(only_known=True)` y responde
  `{"success", "reinjected", "read", "returned_to_queue", "not_returned", "invoiced", "errors"}`.
  Si falla, responde 500 con el mensaje.
- `handle_invoices_and_billing(only_known=False)` devuelve ese resumen.
  También corta temprano si la cola viene vacía; antes fallaba en
  `make_merge`.
- `ConnectionsSodimac.reinyectar_una_oc(oc) -> bool`: `reinyectar_oc` se
  traga los errores y se detiene en el primero.
- `create_orders` (botón y endpoint viejos) responde 410. Leer la cola desde
  allí le quitaría OC a este backend y las duplicaría en Shopify. El código
  anterior queda en `_create_orders_before_cutover`, sin ruta.
- No se reactiva `.github/workflows/sodimac.yml` (vacío desde el
  2026-09-25). `pamo_web` solo corre cuando lo llama este backend.

### Cambios aquí

- `SODIMAC_CUTOVER_DATE` y `SODIMAC_LEGACY_OCS` en `config/constants.py`
  y el filtro por `FECHA_TRANSMISION` en `sync_sodimac_orders`.
- Cliente temporal `integrations/pamo_web/` (transporte), con
  `PAMO_WEB_BASE_URL` y `PAMO_WEB_BOT_TOKEN` (header `X-Bot-Token`) en
  `config/constants.py`. Timeout largo, porque el endpoint es síncrono.
- La llamada va al final de la corrida de Sodimac, como último paso.

### Procedimiento del corte (día D)

1. Desplegar este backend con las migraciones (`orders` 0010–0011,
   `invoicing` 0001–0002). Sin programar todavía los procesos.
2. En `pamo_web`, con la versión actual: correr el proceso completo una
   última vez (`/pamo_bots/sodimac`). Crea las órdenes y factura lo que
   esté en estado final.
3. Desplegar la rama `sodimac-invoices-only` de `pamo_web`. Desde aquí, el
   proceso viejo ya no puede crear órdenes.
4. Variables de entorno aquí:
   - `SODIMAC_SHOPIFY_CUSTOMER_ID` (en `pamo_web`: `7247084421397`);
   - `SODIMAC_CUTOVER_DATE` = D;
   - `SODIMAC_LEGACY_OCS` = las OC de `SodimacOrders` con
     `fecha_transmision = D` (las del mismo día que ya creó `pamo_web`);
   - `PAMO_WEB_BASE_URL` y `PAMO_WEB_BOT_TOKEN` (`BOT_API_TOKEN` de
     `pamo_web`).
5. Primera corrida controlada: lanzar `orders.sync_sodimac` con
   `{"limit": 1}` y revisar la orden en Shopify y el resumen de `pamo_web`.
6. Programar por la API del orquestador, con los horarios de `pamo_web`
   (1:00, 11:00 y 15:00, hora Colombia): `orders.sync_sodimac` a esas horas
   e `invoicing.invoice_sodimac` 30 minutos después.

Entre los pasos 2 y 5 no se debe correr nada en `pamo_web`. Lo que llegue
a la cola en ese intervalo lo lee la primera corrida de aquí.

### Fin de la convivencia

Termina cuando `pamo_web` no tenga OC sin factura desde el 2026-04-01, o
cuando se cumpla el plazo acordado (propuesta: 60 días desde el corte). Lo
que quede se revisa a mano. Entonces se quitan la llamada, el cliente
`integrations/pamo_web/` y sus constantes, y se apaga el proceso en
`pamo_web`.

### Pendiente por revisar

`rpa/` y `stock_dispatch.py` de `pamo_web` leen `SodimacOrders`. Después del
corte no verán OC nuevas. Confirmar si siguen en uso.

## Pruebas

- `products`: `expand_product` con precio (kit con cantidades distintas,
  total conservado).
- `process_shipment`: SKU de kit crea una línea por componente con el
  precio repartido. Se ajusta la prueba actual que esperaba error.
- `sync_sodimac_orders`, con Sodimac y Shopify simulados:
  - OC nueva guardada antes de Shopify;
  - OC repetida en la cola no duplica ítems;
  - actualización de estado;
  - reinyección solo de OC abiertas;
  - fallo de Shopify deja `error_creando_orden`;
  - `limit`.
- `build_sodimac_invoice`: totales iguales a los de `pamo_web` para una OC
  de ejemplo (simple y con kit).
- `invoice_sodimac_orders`, con Siigo simulado:
  - éxito;
  - rechazo → `error` y reintento;
  - rechazo por total calculado → reintento con ese total;
  - excepción de red → queda `creando` y no se reintenta;
  - una OC no se factura dos veces.
- `python manage.py check` y
  `DATABASE_URL= python manage.py test products orders invoicing`.

## Documentación a actualizar

- `docs/apps/invoicing.md` (nuevo), `docs/apps/orders.md`,
  `docs/apps/products.md` (reparto de precio).
- `APP_BOUNDARIES.md` e `INDEX.md` (`invoicing`).
- `docs/apps/orchestrator.md` (procesos nuevos).
- [`products-catalog.md`](products-catalog.md): el reparto deja de estar
  pendiente.

## Riesgos

- **Cola destructiva**: si el proceso muere entre leer y guardar, se pierde
  la OC. Mitigación: guardar cada OC apenas se lee, antes de cualquier
  llamada lenta (Shopify).
- **Factura duplicada**: la mitigan el `OneToOneField` y el estado
  `creando`.
- **Convivencia con `pamo_web`**: los dos procesos leen la misma cola. Se
  mitiga encadenándolos y con las reglas de la Parte C. La primera prueba
  con `limit` se hace con `pamo_web` pausado.
- **El SKU no existe en Siigo**: la factura queda en `error` con el mensaje
  de Siigo (en `pamo_web`: "sku no valido").

## Vacíos resueltos (2026-10-01)

Criterio del usuario: pasar el flujo "tal cual" desde `pamo_web`.

1. **Estado de pago en Shopify**: `PENDING` para Sodimac (paga a crédito),
   como `pamo_web`. `process_shipment` recibe `financial_status`; por
   defecto sigue en `PAID`.
2. **Hasta cuándo se reinyecta**: toda OC abierta, sin límite de
   antigüedad. Todas son posteriores al corte. Si con el tiempo se
   acumulan OC que nunca cierran, se agrega un límite.
3. **Se factura aunque la orden de Shopify haya fallado**, como en
   `pamo_web`.
4. **Horarios**: los de `pamo_web`. Se configuran por la API del
   orquestador (paso 6 del procedimiento), no en código.

## Diferencias con `pamo_web` a tener en cuenta

- Etiqueta en Shopify `sodimac` (como los demás canales). `pamo_web` usaba
  `SODIMAC`: revisar si algún filtro, reporte o automatización de Shopify
  depende de la mayúscula.
- El subtotal de la factura se suma sobre los precios redondeados de las
  líneas (ver [`../apps/invoicing.md`](../apps/invoicing.md)).
- `pamo_web` copiaba el estado de Sodimac en `TrakingOrders`
  (`update_tracking_status`). Para las OC nuevas ese estado ya no se
  actualiza; aquí queda en `marketplace_status`. Pasa lo mismo con el RPA
  (Parte C).
