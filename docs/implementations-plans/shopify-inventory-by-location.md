# Plan: bodega de despacho por pedido según inventario de Shopify

Estado: **implementado (2026-09-23)**. Pendiente: `migrate` de
`orders/migrations/0004_fulfillment_location.py` en Railway, configurar
`FULFILLMENT_PRIORITY_LOCATION_ID` y la prueba en vivo. Desvío respecto al
cambio 1: en vez de crear `GET_VARIANT_INVENTORY_BY_SKU` se **amplió**
`GET_VARIANT_BY_SKU`, siguiendo la convención de
`integrations/shopify/QUERIES.md` (ampliar antes que duplicar); ambas
funciones la usan. Extiende el flujo de
`orders` descrito en [`falabella-fixed-customer.md`](falabella-fixed-customer.md).

## Objetivo

Al resolver los SKUs de un pedido de marketplace, traer en la **misma
consulta** a Shopify las bodegas (`Location`) y sus unidades disponibles, y
elegir **una sola bodega para todo el pedido** — el marketplace (Falabella)
entrega una guía de envío por pedido, así que el pedido completo sale de un
mismo lugar. Si ninguna bodega puede despachar el pedido completo, el pedido
queda como **novedad** para que un usuario decida qué hacer. El resultado se
usará más adelante para avisar por WhatsApp a la bodega.

## Decisiones tomadas

1. **La orden en Shopify se crea siempre**, haya o no bodega (reconfirmado
   por el usuario con la novedad ya definida). La bodega es un dato de
   despacho aparte: nunca bloquea `create_order` ni cambia el `status` del
   pedido.
2. **Una bodega por pedido**: debe tener unidades disponibles suficientes
   para **todos** los ítems del pedido (cantidad de cada SKU ≤ `available`
   en esa bodega).
3. **Prioridad a "Bodega Envia"**: si cubre el pedido completo, se elige.
4. **Si Envia no lo cubre**: entre las demás bodegas que cubren el pedido
   completo, la que tenga más unidades disponibles sumando los SKUs del
   pedido (empate → orden alfabético del nombre, para que el resultado sea
   determinista). Confirmado por el usuario 2026-09-23.
5. **Ninguna bodega cubre el pedido completo → novedad**: no se parte el
   pedido entre bodegas ni se elige una a medias. Queda sin bodega y con
   un motivo legible, para decisión manual.
6. **Ítem que no controla inventario (`tracked: false`) → novedad**: sin
   cantidades no se puede afirmar que una bodega lo cubre. Confirmado por
   el usuario 2026-09-23.
7. **Solo se identifica y guarda**; no se reasigna la bodega en Shopify
   (`fulfillmentOrderMove` queda fuera de alcance).
8. La bodega prioritaria se configura **por id** (los nombres pueden
   cambiar), vía `config/constants.py`.
9. **Resolución de la novedad, por ahora en `/admin/`**: el usuario elige
   la bodega a mano y marca el pedido como resuelto. Un endpoint/pantalla
   dedicada queda para después.

## Dónde se guarda la información

Sin tablas nuevas: campos nuevos en las tablas existentes de `orders`, una
sola migración (detalle en el cambio 5).

| Tabla | Campo | Contenido |
| --- | --- | --- |
| `MarketplaceOrder` | `fulfillment_status` | `asignada` / `novedad` / `resuelta_manual` (vacío = sin evaluar) |
| `MarketplaceOrder` | `fulfillment_location_id` | Id de la `Location` de Shopify (dato estable) |
| `MarketplaceOrder` | `fulfillment_location_name` | Nombre de la bodega, para leer sin consultar Shopify |
| `MarketplaceOrder` | `fulfillment_note` | Motivo de la novedad o nota de quien la resolvió |
| `MarketplaceOrderItem` | `inventory_snapshot` | JSON con el stock por bodega de ese SKU al momento de decidir |

La bodega va en el pedido porque es una por pedido; el stock va en el
ítem porque es por SKU. No se guarda un catálogo de bodegas ni su stock
vigente (vive en Shopify y se consulta en cada corrida). En la fase de
WhatsApp se creará una tabla de bodegas con contacto; el
`fulfillment_location_id` guardado desde ahora es lo que permitirá
relacionarla sin migrar datos.

## Verificado en vivo (2026-09-23, solo lectura)

- El token de Shopify tiene acceso a inventario y ubicaciones: no hacen
  falta permisos nuevos.
- Forma de la respuesta (API 2024-07):
  `productVariant.inventoryItem { tracked inventoryLevels(first: N) { edges { node { location { id name } quantities(names: ["available"]) { name quantity } } } } }`.
  Costo real ≈ 8 puntos por SKU.
- 7 bodegas activas, todas con `fulfillsOnlineOrders: true`: Amazon, Baru,
  Boccherini, Bodega Compa, Bodega Envia, Proveedores, Taumm.
- Un mismo SKU puede tener stock en varias bodegas a la vez (SKU probado:
  45 en "Proveedores", 25 en "Bodega Envia").
- `inventoryLevels` lista solo las bodegas donde el ítem está dado de alta,
  no las 7: una bodega ausente equivale a 0 unidades.

## Cambios

### 1. `integrations/shopify/queries.py` + `QUERIES.md`

Nueva consulta `GET_VARIANT_INVENTORY_BY_SKU`: igual a `GET_VARIANT_BY_SKU`
más `inventoryItem { tracked inventoryLevels(first: 20) { ... location {
id name } quantities(names: ["available"]) { name quantity } } }`. Se deja
`GET_VARIANT_BY_SKU` intacta. Registrar en `QUERIES.md` con fecha de
verificación.

### 2. `integrations/shopify/functions/get_variant_inventory_by_sku.py` (nueva)

Misma verificación de SKU exacto que `get_variant_by_sku`. Devuelve:

```python
{
    "variant_id": "47993005441301",
    "sku": "16164783",
    "tracked": True,
    "locations": [
        {"location_id": "94018535701", "name": "Proveedores", "available": 45},
        {"location_id": "97615380757", "name": "Bodega Envia", "available": 25},
    ],
}
```

o `None` sin coincidencia exacta. Ids sin prefijo `gid://`. Si
`quantities` no trae `available`, tratarlo como 0. Solo normaliza — **no
elige bodega** (regla de negocio, vive en `orders`). `get_variant_by_sku`
no cambia.

### 3. `config/constants.py` + `.env.example`

```python
# Bodega (Location de Shopify) que se prefiere para despachar pedidos de
# marketplace (app orders). Id numérico sin prefijo gid://.
FULFILLMENT_PRIORITY_LOCATION_ID = config("FULFILLMENT_PRIORITY_LOCATION_ID", default="")
```

Vacía → sin prioridad (aplica directamente la decisión 4).

### 4. `orders/functions/select_fulfillment_location.py` (nueva)

```python
select_fulfillment_location(lines, priority_location_id)
# lines: [{"sku", "quantity", "tracked", "locations": [...]}]  (uno por ítem)
# -> {"location_id": str, "name": str, "reason": ""}   bodega elegida
# -> {"location_id": "", "name": "", "reason": "..."}  novedad
```

Función pura, sin I/O, aplicando las decisiones 2–6. El `reason` de una
novedad es legible para el usuario, p. ej. `"Ninguna bodega tiene el pedido
completo: SKU-1 x2 (Bodega Envia 1, Proveedores 5); SKU-2 x1 (Baru 1)"` o
`"SKU-3 no controla inventario en Shopify"`.

### 5. `orders/models.py` + migración

En `MarketplaceOrder`:

```python
class FulfillmentStatus(models.TextChoices):
    PENDING = "", "Sin evaluar"            # pedidos previos a este cambio / aún sin procesar
    ASSIGNED = "asignada", "Bodega asignada"
    NOVEDAD = "novedad", "Novedad"
    RESOLVED = "resuelta_manual", "Resuelta manualmente"

fulfillment_status = models.CharField(max_length=20, choices=FulfillmentStatus.choices, blank=True, default="")
fulfillment_location_id = models.CharField(max_length=32, blank=True)
fulfillment_location_name = models.CharField(max_length=100, blank=True)
fulfillment_note = models.TextField(blank=True)  # motivo de la novedad / nota de quien la resolvió
```

En `MarketplaceOrderItem`:

```python
inventory_snapshot = models.JSONField(default=list, blank=True)  # locations al momento de decidir
```

`fulfillment_status` es independiente de `status` (creación en Shopify):
un pedido puede estar `orden_creada` + `novedad`.

### 6. `orders/functions/process_pending_orders.py`

- En `_resolve_line_items`, reemplazar `get_variant_by_sku` por
  `get_variant_inventory_by_sku`, llamándola **siempre** (aunque
  `shopify_variant_id` ya esté cacheado: el stock cambia). Se mantiene una
  sola llamada a Shopify por ítem. Guardar `shopify_variant_id` (si
  faltaba) e `inventory_snapshot`. SKU sin coincidencia → mismo
  `error_creando_orden` de hoy (sin evaluar bodega).
- Con todos los ítems resueltos, llamar a `select_fulfillment_location` y
  guardar `fulfillment_status` (`asignada` o `novedad`),
  `fulfillment_location_*` y `fulfillment_note`. Luego `create_order`
  igual que hoy (decisión 1).
- No sobrescribir un pedido con `fulfillment_status = resuelta_manual`
  (solo aplica si ese pedido se reintentara por un error de orden).

### 7. `orders/admin.py`

- `list_filter` por `fulfillment_status` y columna con bodega en la lista,
  para encontrar novedades rápido.
- `fulfillment_location_id`, `fulfillment_location_name`,
  `fulfillment_status` y `fulfillment_note` editables; `inventory_snapshot`
  de solo lectura en el inline de ítems, para que quien resuelve vea el
  stock por bodega que se evaluó.

## Pruebas

- `integrations/shopify/tests.py`: normalización con la forma real
  verificada, SKU sin coincidencia exacta, `tracked: false`, sin
  `inventoryLevels`, `quantities` sin `available`.
- `orders/tests.py`:
  - `select_fulfillment_location`: Envia cubre todo → Envia; Envia no
    cubre, otra sí → esa; varias cubren → más unidades (y desempate por
    nombre); cada ítem en bodega distinta, ninguna con todo → novedad con
    motivo; ítem `tracked: false` → novedad; sin prioridad configurada;
    bodega ausente en `inventoryLevels` cuenta como 0.
  - `process_pending_orders`: guarda `asignada` + bodega + snapshot; guarda
    `novedad` y **aun así crea la orden**; consulta inventario aunque el
    variant id esté cacheado; no pisa `resuelta_manual`.

`python manage.py test orders integrations.shopify` y `python manage.py check`.

## Riesgos

- El stock puede cambiar entre la consulta y el despacho real; la bodega
  guardada es la mejor estimación al momento de crear la orden.
- La bodega que asigna Shopify internamente puede diferir de la guardada
  (decisión 7).
- Pedidos ya creados antes de este cambio quedan en "Sin evaluar"; no se
  recalculan.

## Fuera de alcance

- Endpoint/pantalla en el frontend para gestionar novedades (hoy: admin).
- Datos de contacto de cada bodega (WhatsApp por `location_id`), envío del
  aviso y su disparo desde el orquestador.
- Reasignar la bodega en Shopify (`fulfillmentOrderMove`).

## Orden de implementación sugerido

1. Cambios 1–2 (consulta y función de Shopify) con sus pruebas.
2. Cambio 3 (constante) y cambio 4 (regla pura) con sus pruebas — no
   dependen de Shopify.
3. Cambio 5 (modelos + `makemigrations`; **no** ejecutar `migrate` contra
   Railway desde local — `.env` apunta a la base de producción).
4. Cambios 6–7 (integración en `process_pending_orders` y admin).
5. Documentación, `python manage.py test orders integrations.shopify` y
   `python manage.py check`.
6. Despliegue: `migrate` en Railway y configurar
   `FULFILLMENT_PRIORITY_LOCATION_ID` con el id de "Bodega Envia".
7. Prueba en vivo controlada: `import_falabella_orders(params={"limit": 1})`
   y revisar en `/admin/` la bodega/novedad y el snapshot.

## Documentación a actualizar en el mismo cambio

- `integrations/shopify/QUERIES.md`: consulta nueva.
- `docs/apps/integrations.md` y `docs/architecture/INTEGRATIONS.md`:
  función nueva de Shopify.
- `docs/apps/orders.md`: bodega por pedido, regla de selección, novedad y
  cómo resolverla, campos nuevos, pruebas.
- `.env.example`: `FULFILLMENT_PRIORITY_LOCATION_ID` sin valor.
