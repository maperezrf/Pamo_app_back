# App `products`

`products/` es el catálogo de Pamo: productos, kits y equivalencias de SKU
por marketplace. Existe para que cualquier canal de `orders/` traduzca el
SKU que reporta un marketplace a lo que se envía a Shopify. Es general para
todos los marketplaces, no solo para Sodimac.

`products` no depende de `orders`; la dependencia va en sentido
`orders → products`.

## Capacidades

- `Marketplace` (`TextChoices`): lista única de canales (Falabella,
  Mercado Libre, Madecentro, Sodimac). `orders.MarketplaceOrder.Marketplace`
  es un alias de esta clase. Un marketplace nuevo se agrega aquí, y
  aparece solo en el formato de equivalencias.
- `Product`: `sku` (SKU de Pamo, único), `name` (opcional), `is_kit`.
  - En un producto simple, `sku` es el que existe en Shopify.
  - Un kit no existe en Shopify: a Shopify van sus componentes.
  - Los kits traídos de `pamo_web` usan como SKU el de Sodimac
    (`kitnumber`).
- `KitComponent(kit, component, quantity)`: relación muchos a muchos entre
  kit y producto, con cantidad mayor que 0. Es única por `(kit, component)`.
  **Sin kits anidados**: un componente nunca es kit.
- `MarketplaceSku(product, marketplace, sku, ean)`: una fila por
  equivalencia, única por `(marketplace, sku)`. Un producto puede tener
  varios SKU en el mismo canal. `ean` es el que asigna el marketplace.
- `functions/resolve_marketplace_sku.py`: `(marketplace, sku) → Product | None`.
  Quita espacios a los lados.
- `functions/expand_product.py`: `expand_product(product, quantity, unit_price=None) → [{"sku", "quantity"[, "price"]}]`,
  los SKU de Shopify con su cantidad.
  - Kit: multiplica la cantidad pedida por la de cada componente.
  - Kit sin componentes: `EmptyKitError`.
  - Con `unit_price` (precio de una unidad del producto), cada línea lleva
    `price` (`Decimal`, 2 decimales, redondeo half-up). En un kit, el
    precio se reparte **en partes iguales por unidad de componente**:
    `unit_price / Σ cantidades del kit`, como en `pamo_web`. El total se
    conserva salvo el redondeo por línea. Es la única regla de reparto:
    la usan `orders.process_shipment` (precio en Shopify) e `invoicing`
    (factura en Siigo).
- `functions/export_equivalences.py` / `import_equivalences.py`:
  equivalencias en formato de columnas (`pamo_sku`, `<marketplace>_sku`,
  `<marketplace>_ean`).
  - Varios SKU de un mismo canal se expresan repitiendo la fila.
  - La carga crea o actualiza por `(marketplace, sku)`: mueve el SKU si
    apuntaba a otro producto (lo informa en `moved`) y nunca borra.
  - Una columna desconocida lanza `InvalidColumnsError`.
- `functions/export_kits.py` / `import_kits.py`: kits con una fila por
  componente (`kit_sku`, `component_sku`, `quantity`).
  - La carga **reemplaza completo** cada kit que viene.
  - Si una fila del kit es inválida, ese kit no se toca.
- `functions/normalize_cell.py`: limpia celdas de Excel (`54654.0` →
  `"54654"`).
- Comando `import_pamo_web_catalog --products <csv> --kits <csv>`:
  importación única desde los Excel de `pamo_web` guardados como CSV (`,` o
  `;`). Se puede repetir. Consulta Shopify en modo lectura y descarta filas
  invertidas y kits que no funcionarían (reglas en el plan).
- Admin: `Product` con sus equivalencias y componentes como inlines;
  `MarketplaceSku` con búsqueda propia; `SkuUpload` de solo lectura.
- **Carga de equivalencias de UN marketplace validada contra Shopify**:
  - `SkuUpload(marketplace, uploaded_by, execution_id, rows, results, summary, created_at, finished_at)`.
    Guarda la entrada y el reporte por fila. El estado y el progreso no se
    duplican aquí: vienen de `orchestrator.services.get_execution_status`.
  - Proceso `products.upload_sku_equivalences`
    (`functions/upload_sku_equivalences.py`), siempre en segundo plano.
    Corre en tres fases:
    1. Valida vacíos, largos, `sku_marketplace` repetidos en el archivo (gana
       la primera fila) y kits.
    2. Consulta Shopify por bloques con
       `integrations.shopify.get_variants_by_skus`. Un bloque que falla se
       reintenta una vez; si vuelve a fallar, sus filas quedan para
       reintentar.
    3. Aplica cada fila en su propia transacción.
  - Reglas: el `sku_pamo` debe existir en Shopify. Si existe en Shopify y no
    en `Product`, se crea el producto. Si está en `Product` y no en Shopify,
    queda como alerta y no se crea nada. Un `sku_marketplace` que apuntaba a
    otro producto se **reasigna** (alerta `Reasignado: antes apuntaba a X`).
    **EAN vacío no borra el existente**, a diferencia de la carga de
    equivalencias. Los kits se rechazan: tendrán su propia carga.
  - Cada fila del reporte conserva todas sus columnas y agrega `resultado`
    (texto) y `resultado_codigo` (`ok` / `alert` / `error`). `summary` trae
    `total`, `por_codigo` y `por_caso`. El reporte se guarda también si la
    carga se cancela o falla; las filas sin procesar quedan marcadas.
  - Roles: `SKU_UPLOAD_ROLES = ["Admin"]`, separado de `CATALOG_ROLES`.
    Tope: `SKU_UPLOAD_MAX_ROWS = 2000`.

## API

Todas las rutas requieren el rol `Admin` (`RoleRequiredMixin`). Contrato en
[`../contracts/API.md`](../contracts/API.md).

- `GET /api/products/`: lista paginada (100 por página), con `?search=`.
- `GET/POST /api/products/equivalences/` y `GET/POST /api/products/kits/`:
  el `GET` devuelve `{"columns", "rows"}` para que el frontend arme el
  Excel; el `POST` recibe `{"rows": [...]}` leídas del Excel. **El backend
  no genera ni lee archivos.**

Resultado de una carga:

```
{"created", "updated", "errors": [{"row", "error"}]}
```

Las equivalencias agregan `products_created` y `moved`. `row` es la posición
1-based en `rows`. Cada carga corre en una transacción.

- `POST /api/products/sku-uploads/` (`{marketplace, rows}` → `202 {id, execution_id}`),
  `GET /api/products/sku-uploads/<id>/` (estado, progreso, `summary` y
  `rows` con el reporte; `null` hasta terminar) y
  `GET /api/products/sku-uploads/` (historial paginado de 20, sin `rows`).
  Rol `SKU_UPLOAD_ROLES`. El frontend hace polling sobre el detalle y arma
  el Excel del reporte con `rows`.

## Límites

- La carga de equivalencias y la de kits no validan contra Shopify que los
  SKU existan; un componente inexistente se detecta al crear la orden. La
  carga por marketplace (`sku-uploads`) **sí** valida cada `sku_pamo`
  contra Shopify.
- Sin stock ni inventario: el de Sodimac quedó pendiente por un problema
  del lado de Sodimac.
- `orders/functions/process_shipment.py` consume el catálogo para todos
  los canales que crean órdenes en Shopify (Falabella, Mercado Libre,
  Madecentro).
  - Traduce equivalencias de productos simples.
  - Sin equivalencia, usa el SKU tal cual.
  - Una equivalencia a kit va como una línea por componente, con el precio
    repartido por `expand_product` (ver [`orders.md`](orders.md)).
- `invoicing/functions/build_sodimac_invoice.py` aplica las mismas reglas
  a las líneas de la factura de Sodimac (ver [`invoicing.md`](invoicing.md)).

## Pruebas

```
python manage.py test products
```

Cubren las funciones, las cargas (ida y vuelta, errores de fila, kits
anidados), la API (camino feliz con `Admin` y `403` sin sesión o sin rol) y
el comando con CSV en formato `pamo_web`. La carga por marketplace, con
Shopify simulado, cubre:
- cada caso del reporte;
- la carga mixta con su `summary`;
- las columnas extra que se conservan;
- el EAN vacío que no borra;
- el bloque que falla dos veces sin frenar a los demás;
- el reintento de un bloque;
- la cancelación a mitad con resultados parciales;
- el error inesperado que guarda el reporte.

La API cubre:
- `202` con `Admin`;
- `403` sin sesión o sin rol;
- `400` por marketplace, filas vacías, columna faltante o tope;
- el detalle con el estado del orquestador;
- `404`;
- el historial paginado.

En local, correrlas con `DATABASE_URL` vacío para usar SQLite: con
`DATABASE_URL` definido, el proyecto se conecta a Postgres de Railway.
