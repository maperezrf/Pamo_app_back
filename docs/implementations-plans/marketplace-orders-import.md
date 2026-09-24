# Plan: importar pedidos de marketplaces a Shopify (app `orders`)

Estado: **implementado y probado** (modelos, las 3 funciones, registro en
`orchestrator`, migración de datos para el `ProcessType`, documentación).
`python manage.py test` completo en verde (119 tests). Pendiente: registrar
la programación real (horarios) vía la API del orquestador — eso lo decide
quien opere el sistema, no queda fijo en código — y el riesgo de reintento
duplicado (ver más abajo, sigue sin resolver). Este documento reemplaza el
diseño original de la Fase 3 en
[`falabella-to-shopify-orders-import.md`](falabella-to-shopify-orders-import.md)
(que proponía un único modelo `FalabellaOrderImport` como ledger simple) por
un diseño más general, decidido explícitamente para que un marketplace
futuro (Mercado Libre, etc.) reutilice la misma estructura sin duplicarla.

> **Actualización (2026-09-23)**: la resolución y creación de clientes en
> Shopify (decisión 6, las filas de cliente de la tabla de reutilización y
> la etapa `reconcile_from_shopify`) quedó reemplazada por un cliente fijo.
> Ver [`falabella-fixed-customer.md`](falabella-fixed-customer.md).

## Decisiones tomadas en esta conversación

1. **Disparo**: un proceso en `orchestrator`, programado varias veces al día
   (no una sola corrida diaria como `customers.reconcile_shopify`) — los
   horarios exactos se configuran después vía la API del orquestador
   (`POST /api/orchestrator/schedules/`), no se fijan en código.
2. **Qué está "pendiente"**: cualquier `MarketplaceOrder` sin
   `shopify_order_id` — nuevo o de una corrida anterior que terminó en
   error. No hay checkpoint de fechas: el indicador es el estado del dato
   local, no un rango temporal. Esto da reintento automático gratis (ver
   "Riesgo real" más abajo, matiz importante).
3. **Resolución de SKU**: en vivo, con
   `integrations.shopify.functions.get_variant_by_sku.get_variant_by_sku()`
   (ya existe, ya hace *exact match*) contra el `Sku` que manda el
   marketplace. **Sin tabla de equivalencias** — pospuesto a propósito (ver
   "Pospuesto explícitamente" abajo).
4. **Número de orden del marketplace**: va en el `note` de la orden de
   Shopify (`create_order(..., note=...)`), no en un campo dedicado — no se
   verificó si Shopify tiene uno propio para referencia externa, y se
   decidió no perder tiempo en eso ahora.
5. **Errores no bloquean el lote**: un pedido que falla queda marcado, el
   proceso sigue con el resto.
6. **Comprador sin email ni teléfono**: confirmado real, no hipotético — el
   2026-09-19 se trajeron 2 pedidos reales de Falabella (`ShippingType:
   Dropshipping`) y **ninguno de los dos traía `CustomerEmail` ni `Phone`**
   en `AddressBilling`/`AddressShipping`, solo nombre, cédula y dirección
   física. `create_customer()` exige uno de los dos — se decidió marcar el
   pedido como `error_creando_cliente` en vez de inventar un dato de
   contacto.

## Pospuesto explícitamente: modelo de equivalencias de SKU

La idea real (`sku_shopify` / `sku_falabella` / `sku_sodimac` / ...) queda
fuera de esta fase por decisión del usuario — tiene matices propios (un SKU
de marketplace no siempre coincide con el de Shopify, ya lo vimos en un
pedido real: `Sku "31200513"` vs `ShopSku "133919574"`, distinto al caso
que se había "confirmado" antes con `"15L907086-N"`) que no se van a
resolver de forma apurada. `MarketplaceOrderItem` (abajo) ya queda listo
para conectarlo después: el día que exista la tabla de equivalencias, solo
cambia de dónde sale `shopify_variant_id` (de la tabla en vez de
`get_variant_by_sku` en vivo) — no hace falta tocar el modelo.

## Modelos (`orders/models.py`)

**Ajuste hecho al implementar**: el diseño de abajo no tenía forma de
saber el nombre/apellido/email del comprador al momento de crear el
cliente en Shopify (`process_pending_orders` los necesita para
`create_customer`). Se agregaron `customer_first_name`,
`customer_last_name` y `customer_email` a `MarketplaceOrder` junto a
`customer_identification` — vacío en el plan original, no un cambio de
diseño de fondo.

```python
class MarketplaceOrder(models.Model):
    class Marketplace(models.TextChoices):
        FALABELLA = "falabella", "Falabella"
        # futuros marketplaces se agregan acá, no se crea un modelo por canal

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ERROR_CUSTOMER = "error_creando_cliente", "Error al crear cliente"
        ERROR_ORDER = "error_creando_orden", "Error al crear orden"
        CREATED = "orden_creada", "Orden creada"

    marketplace = models.CharField(max_length=20, choices=Marketplace.choices)
    marketplace_order_id = models.CharField(max_length=64)       # OrderId (interno)
    marketplace_order_number = models.CharField(max_length=64, blank=True)  # OrderNumber (visible)
    customer_identification = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    error_description = models.TextField(blank=True)
    shopify_customer_id = models.CharField(max_length=32, blank=True)
    shopify_order_id = models.CharField(max_length=32, blank=True)
    shopify_order_name = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["marketplace", "marketplace_order_id"], name="unique_marketplace_order")
        ]


class MarketplaceOrderItem(models.Model):
    order = models.ForeignKey(MarketplaceOrder, related_name="items", on_delete=models.CASCADE)
    marketplace_sku = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.CharField(max_length=32, blank=True)
    shopify_variant_id = models.CharField(max_length=32, blank=True)  # resuelto por get_variant_by_sku
```

`UniqueConstraint(marketplace, marketplace_order_id)` es lo que evita
duplicar un pedido si `fetch_falabella_orders` corre dos veces sobre el
mismo rango.

## Reutilización — nada nuevo se construye en `integrations/`

Todo lo que necesita esta fase ya existe y ya está probado:

| Paso | Función que ya existe |
| --- | --- |
| Refrescar directorio de clientes | `customers.functions.reconcile_from_shopify.reconcile_from_shopify` |
| Listar pedidos | `integrations.falabella.functions.get_orders.get_orders` |
| Ítems de un pedido | `integrations.falabella.functions.get_order_items.get_order_items` |
| Buscar cliente local | `customers.functions.find_by_identification.find_by_identification` |
| Crear cliente en Shopify | `integrations.shopify.functions.create_customer.create_customer` |
| Escribir la cédula del cliente | `integrations.shopify.functions.create_customer_address.create_customer_address` |
| Resolver SKU → variante | `integrations.shopify.functions.get_variant_by_sku.get_variant_by_sku` |
| Crear la orden | `integrations.shopify.functions.create_order.create_order` |

## Funciones nuevas (`orders/functions/`)

- **`fetch_falabella_orders.py`**: `get_orders(...)` + `get_order_items(...)`
  por cada pedido; crea `MarketplaceOrder`/`MarketplaceOrderItem` para los
  que todavía no existan (`get_or_create` por `marketplace` +
  `marketplace_order_id`). No toca Shopify para nada — solo persiste lo que
  Falabella reporta.
- **`process_pending_orders.py`**: recorre
  `MarketplaceOrder.objects.filter(shopify_order_id="")` (nuevos + errores
  previos, sin distinción). Por cada uno:
  1. `find_by_identification(customer_identification)` → si no existe:
     `create_customer` + `create_customer_address`. Si falta email/teléfono
     → `status=error_creando_cliente`, `error_description` con el detalle,
     `continue` al siguiente pedido.
  2. Por cada `MarketplaceOrderItem`: `get_variant_by_sku(marketplace_sku)`
     → si algún ítem no resuelve → `status=error_creando_orden`,
     `error_description="SKU no encontrado: <sku>"`, `continue`.
  3. `create_order(items=..., customer_id=..., tags=[marketplace],
     financial_status="PAID", note=f"{marketplace} #{marketplace_order_number}")`
     → guardar `shopify_order_id`/`shopify_order_name`, `status=orden_creada`.
     `financial_status` fijo en `"PAID"`, decidido al implementar (no
     estaba en el plan original): un pedido de marketplace llega ya pagado
     por el comprador, Shopify solo registra la venta, no cobra nada.
- **`import_falabella_orders.py`**: el proceso registrado en
  `orchestrator` — llama `reconcile_from_shopify()` →
  `fetch_falabella_orders()` → `process_pending_orders()`, reportando
  progreso en cada etapa. Firma:
  `(params, progress_callback=None, cancellation_token=None)`.

## Registro en `orchestrator`

- `orchestrator/registrations.py` — `register_process("orders.import_falabella",
  import_falabella_orders)`. (El plan original lo ponía en
  `orders/apps.py::ready()`; se centralizó: todos los procesos se registran
  en `registrations.py`, ver `docs/apps/orchestrator.md`.)
- `orders/migrations/0002_seed_process_types.py` — data migration que da de
  alta `ProcessType(code="orders.import_falabella", allow_concurrent=False)`
  (mismo patrón que `customers/migrations/0002_seed_process_types.py`).
  `allow_concurrent=False` a propósito: dos corridas a la vez sobre los
  mismos pedidos pendientes podrían duplicar la creación en Shopify.
- `orders/admin.py` — `MarketplaceOrder` con `MarketplaceOrderItem` inline,
  para que el equipo vea estado y `error_description` sin necesitar un
  endpoint propio.
- Sin endpoint HTTP dedicado por ahora — se dispara manual vía el endpoint
  genérico del orquestador o por su programación. Si hace falta uno propio
  después, sigue el patrón de `docs/patterns/orchestrator-usage-guide.md`.

## Riesgo real a decidir (no bloqueante, pero hay que ser explícito)

`create_order()` ya documenta que si la llamada falla por red/timeout, **no
se sabe si Shopify llegó a crear la orden** — `orderCreate` no tiene clave
de idempotencia propia. Con el diseño de "reintenta todo lo que no tenga
`shopify_order_id`", un pedido que cayó en esa zona gris se **reintentaría
en la próxima corrida sin distinción**, arriesgando una orden duplicada en
Shopify si la primera sí se había creado.

No lo resuelvo por mi cuenta en este plan porque implica una decisión de
negocio (¿aceptar el riesgo de duplicado ocasional a cambio de simplicidad,
o agregar un tercer estado tipo `resultado_desconocido` que bloquee el
reintento automático hasta verificación manual, como sí se hace en otros
proyectos con Siigo?) — queda como punto abierto explícito para la próxima
conversación, no para esta implementación inicial salvo que se decida
ahora.

## Documentación a actualizar en el mismo cambio

- `docs/apps/orders.md`: **hecho** — expediente nuevo.
- `docs/architecture/APP_BOUNDARIES.md`: **hecho** — fila nueva `orders`.
- `docs/INDEX.md`: **hecho** — fila nueva.
- `docs/architecture/INTEGRATIONS.md` / `docs/apps/integrations.md`: sin
  cambios — esta fase no agregó funciones nuevas a `integrations/`, solo
  reutilizó lo existente.
- `falabella-to-shopify-orders-import.md`: **hecho** — Fase 3 apunta a este
  documento.

## Puntos abiertos

1. **¿Se importan pedidos en cualquier estado de Falabella, o solo algunos?**
   `get_orders()` ya normaliza `status`/`status_raw` (`STATUS_MAP` en
   `integrations/falabella/constants.py`), pero `fetch_falabella_orders`
   tal como se implementó **no filtra por estado** — importa un pedido
   `pending` o `cancelled` igual que uno `delivered`. Sigue sin decidirse
   si hace falta un filtro (ej. no crear la orden en Shopify hasta que
   Falabella lo marque como confirmado/pagado) — no se filtró por no
   inventar un criterio de negocio sin que lo confirme el usuario.
2. El riesgo de reintento/duplicado descrito arriba — **sigue sin
   resolver**, implementado tal como se documentó (acepta el riesgo por
   ahora).
3. ~~Ventana de fechas de `get_orders()` en cada corrida~~ — resuelto al
   implementar: lookback fijo de 48h con solape
   (`orders/functions/fetch_falabella_orders.py::LOOKBACK`), confiando en
   el `UniqueConstraint` para no duplicar filas locales.
4. Horarios de la programación ("horas específicas del día") — no se
   crearon `ProcessScheduleConfig` todavía; se registran después vía
   `POST /api/orchestrator/schedules/` (ver
   `docs/patterns/orchestrator-usage-guide.md`), es una decisión operativa
   de quien vaya a correr esto en producción, no de código.
