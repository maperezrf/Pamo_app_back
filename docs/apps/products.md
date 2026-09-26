# App `products`

`products/` es el catálogo de Pamo: productos, kits y equivalencias de SKU
por marketplace. Existe para que cualquier canal de `orders/` traduzca el
SKU que reporta un marketplace a lo que se envía a Shopify. Es general para
todos los marketplaces, no solo para Sodimac. El plan y sus decisiones están
en
[`../implementations-plans/products-catalog.md`](../implementations-plans/products-catalog.md).

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
- `functions/expand_product.py`: `expand_product(product, quantity) → [{"sku", "quantity"}]`,
  los SKU de Shopify con su cantidad.
  - Kit: multiplica la cantidad pedida por la de cada componente.
  - Kit sin componentes: `EmptyKitError`.
  - No reparte precios: eso es regla de la orden de cada canal.
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
  `MarketplaceSku` con búsqueda propia.

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

## Límites

- No valida contra Shopify que los SKU existan. Un componente inexistente se
  detecta al crear la orden.
- Sin stock ni inventario: el de Sodimac quedó pendiente por un problema
  del lado de Sodimac.
- `orders/functions/process_shipment.py` consume el catálogo para todos
  los canales que crean órdenes en Shopify (Falabella, Mercado Libre,
  Madecentro).
  - Traduce equivalencias de productos simples.
  - Sin equivalencia, usa el SKU tal cual.
  - Una equivalencia a kit deja el pedido en error hasta que se defina el
    reparto de precio (ver [`orders.md`](orders.md)).
  - `expand_product` todavía no se usa desde `orders`.

## Pruebas

```
python manage.py test products
```

Cubren las funciones, las cargas (ida y vuelta, errores de fila, kits
anidados), la API (camino feliz con `Admin` y `403` sin sesión o sin rol) y
el comando con CSV en formato `pamo_web`.

En local, correrlas con `DATABASE_URL` vacío para usar SQLite: con
`DATABASE_URL` definido, el proyecto se conecta a Postgres de Railway.
