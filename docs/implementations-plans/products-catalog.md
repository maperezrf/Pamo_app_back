# Plan: catálogo de productos, equivalencias de SKU y kits

Estado: **implementado (2026-09-25), sin desplegar ni importar datos**. Primer paso de la migración de
Sodimac desde `pamo_web` (ver "Contexto"). Las decisiones de este plan las
confirmó el usuario el 2026-09-25.

## Contexto

El proceso de Sodimac vive hoy en el repositorio `pamo_web` (Django aparte,
desplegado en Railway). Para convertir una orden de Sodimac en una orden de
Shopify usa dos tablas **exclusivas de Sodimac**:

- `pamo_bots.ProductsSodimac(sku_sodimac único, sku_pamo, ean, stock, stock_sodi)`:
  traduce el SKU de Sodimac al SKU de Pamo. `sku_pamo` **no** es único.
- `quote_print.SodimacKits(kitnumber, sku, quantity, ean)`: abre un kit en sus
  componentes (una fila por componente). `kitnumber` es **el SKU que usa
  Sodimac**. Sodimac no sabe que es un kit: solo reporta "se vendió una
  unidad de este SKU". `sku` es el SKU de Pamo del componente, el que existe
  en Shopify.

Antes de crear la orden en Shopify, `pamo_web` traduce los SKU que tienen
equivalencia (`make_merge`). Los que no la tienen conservan el SKU de
Sodimac, y así los kits cruzan por `kitnumber` (`set_kits` +
`normalice_kits`).

Este repositorio todavía no tiene catálogo. La tabla de equivalencias quedó
pospuesta a propósito en
[`marketplace-orders-import.md`](marketplace-orders-import.md) ("Pospuesto
explícitamente: modelo de equivalencias de SKU"). Este plan la resuelve de
forma **general para todos los marketplaces**, no solo para Sodimac.

## Objetivo

1. App nueva `products` (catálogo de Pamo), dueña de productos, kits y
   equivalencias de SKU por marketplace.
2. Funciones reutilizables para que cualquier canal de `orders/` resuelva
   "SKU del marketplace → SKU(s) de Shopify con cantidad".
3. API JSON de descarga y carga de equivalencias y kits. El frontend arma y
   lee el Excel; el backend no genera archivos.
4. Importación única de los datos actuales de `pamo_web`.

Fuera de este paso:

- El proceso de Sodimac (importar, seguir estado, facturar).
- El stock hacia Sodimac: pendiente por un problema del lado de Sodimac.
- Conectar Falabella, Madecentro y Mercado Libre al catálogo.

## Por qué una app nueva

Revisado contra [`APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md):
producto y kit son datos de negocio, así que no pueden vivir en
`integrations/`. Tampoco son de `orders/`: `orders/` los **consume**, igual
que lo hará cualquier otra área. Ninguna área existente es dueña del
catálogo.

Dirección de dependencias: `orders → products`. `products` no importa nada
de `orders`.

## Modelo

```python
# products/models.py

class Marketplace(models.TextChoices):
    FALABELLA = "falabella", "Falabella"
    MERCADOLIBRE = "mercadolibre", "Mercado Libre"
    MADECENTRO = "madecentro", "Madecentro"
    SODIMAC = "sodimac", "Sodimac"


class Product(models.Model):
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    is_kit = models.BooleanField(default=False)


class KitComponent(models.Model):
    kit = models.ForeignKey(Product, related_name="components", on_delete=models.CASCADE)
    component = models.ForeignKey(Product, related_name="used_in_kits", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    # UniqueConstraint(kit, component)


class MarketplaceSku(models.Model):
    product = models.ForeignKey(Product, related_name="marketplace_skus", on_delete=models.CASCADE)
    marketplace = models.CharField(max_length=32, choices=Marketplace.choices)
    sku = models.CharField(max_length=64)
    ean = models.CharField(max_length=20, blank=True)
    # UniqueConstraint(marketplace, sku)
```

Decisiones:

- **Filas, no columnas** (`sku_sodimac`, `sku_madecentro`…): un marketplace
  nuevo no requiere migración, y un producto puede tener varios SKU en el
  mismo canal. El usuario confirmó que eso puede pasar en otras plataformas.
  La API sí presenta las equivalencias en columnas (ver abajo).
- **`Product.sku` es el SKU de Pamo.** En un producto simple es el que existe
  en Shopify. Un kit (`is_kit=True`) no existe en Shopify: a Shopify van sus
  componentes. Un kit puede tener equivalencias en varios marketplaces si es
  el mismo paquete.
- **Los kits que vienen de `pamo_web` usan como SKU de Pamo el SKU de
  Sodimac** (`kitnumber`), porque allá no tienen otro nombre. Se crean como
  `Product(sku=kitnumber, is_kit=True)` + `MarketplaceSku(sodimac, kitnumber, ean)`.
  El SKU del kit se puede renombrar después; la equivalencia no cambia.
- **El EAN va en `MarketplaceSku`**, no en `Product`: es el código que
  asigna Sodimac.
- **`Marketplace` se mueve a `products`**, porque `products` no puede
  importar de `orders`. `MarketplaceOrder.Marketplace` pasa a ser un alias
  de `products.models.Marketplace`. Agregar `SODIMAC` genera en `orders` una
  migración que no modifica la base.
- **Sin kits anidados**: un componente no puede ser kit, y un producto que
  ya es componente de otro kit no puede volverse kit. `pamo_web` tampoco
  soporta kits anidados.
- **`name` es opcional**: `pamo_web` no guarda nombres.
- **Sin stock** hasta que se retome el inventario hacia Sodimac.

## Funciones (`products/functions/`, una por archivo)

| Función | Contrato |
| --- | --- |
| `resolve_marketplace_sku(marketplace, sku)` | Devuelve el `Product` o `None`. Quita espacios a los lados del SKU (`pamo_web` hace `strip`). |
| `expand_product(product, quantity)` | Devuelve `[{"sku", "quantity"}]` con los SKU que van a Shopify. Producto simple: `[{product.sku, quantity}]`. Kit: un elemento por componente, con `quantity × componente.quantity`. Un kit sin componentes lanza `EmptyKitError`. |
| `export_equivalences()` / `import_equivalences(rows)` | Descarga y carga de equivalencias (ver "API"). |
| `export_kits()` / `import_kits(rows)` | Descarga y carga de kits. |

Repartir el costo del kit entre sus componentes **no** va aquí. Es una regla
de la orden (precio en Shopify y factura) y se define en el paso de Sodimac.
En `pamo_web`, el costo del kit se divide en partes iguales por unidad de
componente.

## API

`products/urls.py` montado en `config/urls.py` como `/api/products/`.
Permiso en todas las rutas: `RoleRequiredMixin` con `allowed_roles = ["Admin"]`.

| Método y ruta | Entrada | Respuesta |
| --- | --- | --- |
| `GET /api/products/` | Query `search` (SKU de Pamo o de marketplace) y `page` | Página de productos con `marketplace_skus` y, si son kit, `components`. |
| `GET /api/products/equivalences/` | — | `{"columns": [...], "rows": [...]}` en formato de columnas. |
| `POST /api/products/equivalences/` | `{"rows": [...]}` con el mismo formato | Resultado de la carga. |
| `GET /api/products/kits/` | — | `{"columns": [...], "rows": [...]}`. |
| `POST /api/products/kits/` | `{"rows": [...]}` | Resultado de la carga. |

El frontend descarga el `GET` como Excel y, al cargar, lee el Excel y envía
sus filas al `POST`. Las ediciones puntuales se hacen desde Django admin
(`Product` con `MarketplaceSku` y `KitComponent` como inlines). Un CRUD para
el frontend queda para cuando lo pida el front.

### Equivalencias

Una fila por producto, con columnas `pamo_sku` y, por cada marketplace,
`<marketplace>_sku` y `<marketplace>_ean`:

```json
{"pamo_sku": "pamo123", "sodimac_sku": "54654", "sodimac_ean": "7701234567890",
 "madecentro_sku": "PM789", "madecentro_ean": ""}
```

- **Descarga**: incluye todos los productos, aunque no tengan equivalencias.
  Si un producto tiene varios SKU en un mismo canal, **se repite la fila**
  con el mismo `pamo_sku`.
- **Carga**: exige `pamo_sku` en cada fila. Acepta cualquier subconjunto de
  columnas de marketplace. Una columna desconocida (por ejemplo, un error de
  tipeo) rechaza la solicitud completa con `400`.
- Por cada `<marketplace>_sku` con valor, crea o actualiza la equivalencia
  por `(marketplace, sku)`, y crea el `Product` si no existe.
  - Si el SKU del marketplace ya apuntaba a otro producto, **lo mueve** y lo
    informa en `moved`.
  - El `<marketplace>_ean` se escribe solo si la columna viene; vacío lo
    borra.
  - **No borra** equivalencias que no vienen en la carga.
- Si el mismo `(marketplace, sku)` aparece dos veces en la carga con
  productos distintos, se rechazan las repeticiones.

### Kits

Una fila por componente:

```json
{"kit_sku": "5864", "component_sku": "pamo123", "quantity": 1}
```

- **Carga**: por cada `kit_sku`, **reemplaza completo** su conjunto de
  componentes; quitar una fila quita ese componente del kit. Crea los
  `Product` que falten y marca `is_kit=True` en el kit. Los kits que no
  vienen en la carga no se tocan.
- Si **cualquier** fila de un kit es inválida, ese kit entero no se toca,
  para no dejarlo a medias, y se informa. Una fila es inválida si:
  - la cantidad no es un entero mayor que 0;
  - el componente es un kit;
  - el kit es componente de otro kit;
  - el kit se incluye a sí mismo;
  - el componente está repetido dentro del kit.
- Un kit así creado no tiene equivalencia de marketplace. La equivalencia se
  carga por equivalencias, con el kit como `pamo_sku`.

### Resultado de una carga

```json
{"created": 3, "updated": 5, "products_created": 1,
 "moved": [{"marketplace": "sodimac", "sku": "54654", "from": "pamo1", "to": "pamo2"}],
 "errors": [{"row": 4, "error": "..."}]}
```

- `row` es la posición 1-based en `rows`. Si el Excel tiene encabezado, la
  fila del Excel es `row + 1`.
- `products_created` y `moved` solo aplican a equivalencias. `updated`
  cuenta solo lo que realmente cambió: una carga idéntica devuelve 0 y 0.
- Cada carga corre en **una transacción**: un error inesperado no deja nada
  a medias; los errores de validación de fila no la abortan.
- `400` si el cuerpo no trae `rows` como lista de objetos.

## Importación desde `pamo_web`

Comando único:

```
python manage.py import_pamo_web_catalog --products productos.csv --kits kits.csv
```

- Recibe los Excel que ya descarga `pamo_web`, guardados como CSV
  (`productos_sodimac`: `sku_sodimac, sku_pamo, ean`; `kits_sodimac`:
  `kitnumber, ean, sku, quantity`). No hace falta acceso a esa base ni una
  dependencia para leer Excel.
- `productos_sodimac` → fila de equivalencias `{pamo_sku: sku_pamo, sodimac_sku: sku_sodimac, sodimac_ean: ean}`.
  Las filas sin `sku_pamo` se omiten y se listan.
- `kits_sodimac` → filas de kits `{kit_sku: kitnumber, component_sku: sku, quantity}`
  más la equivalencia del kit `{pamo_sku: kitnumber, sodimac_sku: kitnumber, sodimac_ean: ean}`.
- Reglas de limpieza (decididas por el usuario el 2026-09-25). El comando
  consulta Shopify **solo en modo lectura** (`get_variant_by_sku`, con
  caché) y lista todo lo que omite:
  - **Filas invertidas**: `pamo_web` tiene pares cargados en los dos
    sentidos (`795744;7809` y `7809;795744`). Se omite la fila cuyo
    `sku_sodimac` no parece de Sodimac (número de 6 o más dígitos) y cuyo
    `sku_pamo` sí, cuando el par correcto también viene.
  - **Código de Sodimac que es producto y kit a la vez**: gana el producto
    si su SKU existe en Shopify (como en `pamo_web`, donde `make_merge`
    corre antes que `set_kits`; caso `390349` → `5092`). Si no existe, gana
    el kit, porque en `pamo_web` esas órdenes fallaban con "SKU no
    encontrado".
  - **Kit con algún componente que no existe en Shopify**: se descarta
    completo. Una orden de ese kit falla por SKU sin equivalencia y se
    revisa a mano; nunca se despacha un kit incompleto.
- Carga del 2026-09-25 en la base de Railway del `.env`:
  - 1.222 equivalencias de productos (32 filas invertidas omitidas).
  - 89 kits, con 238 componentes y 89 equivalencias de Sodimac (10 de
    ellas movidas desde un producto que no existe en Shopify).
  - 20 kits descartados: 19 por componentes que no existen en Shopify y
    `390349`, que queda como producto `5092`.
- Contra una base remota el comando tarda varios minutos, porque hace
  consultas fila por fila.
- Reutiliza `import_equivalences` e `import_kits`, así que se puede repetir
  sin duplicar. Imprime el resultado de ambas cargas.
- Un EAN leído como número (`7701234567890.0`) se normaliza quitando `.0`.

## Pruebas (`products/tests.py`)

- `resolve_marketplace_sku`: encuentra, no encuentra, SKU con espacios.
- `expand_product`: producto simple, kit con cantidades distintas y
  multiplicación por la cantidad pedida, kit vacío.
- Equivalencias:
  - alta, actualización y SKU que cambia de producto (`moved`);
  - varias filas del mismo producto;
  - EAN ausente frente a EAN vacío;
  - columna desconocida → `400`;
  - duplicado dentro de la carga;
  - ida y vuelta: descargar y volver a cargar sin cambios.
- Kits:
  - alta y reemplazo completo;
  - cantidad inválida (el kit no se toca);
  - componente que es kit;
  - kit que es componente;
  - ida y vuelta.
- API: camino feliz con rol `Admin` y `403` sin sesión y sin rol en cada
  endpoint.
- Comando de importación con dos CSV pequeños en formato `pamo_web`.
- `python manage.py check` y `python manage.py test products orders`.
  `orders` se incluye por el alias de `Marketplace`.

## Documentación que actualiza el desarrollador

- `docs/apps/products.md` (expediente nuevo).
- [`APP_BOUNDARIES.md`](../architecture/APP_BOUNDARIES.md) e
  [`INDEX.md`](../INDEX.md): fila de `products`.
- [`contracts/API.md`](../contracts/API.md): los endpoints.
- [`apps/orders.md`](../apps/orders.md): `Marketplace` ahora vive en
  `products`.
- [`marketplace-orders-import.md`](marketplace-orders-import.md): la sección
  "Pospuesto" apunta a este plan.

## Riesgos

- **Una carga mal armada sobrescribe equivalencias** en producción, porque
  la carga mueve SKU entre productos. Mitigación: la respuesta lista cada SKU
  movido, y una descarga previa sirve de respaldo.
- **Kits con componentes que no existen en Shopify**: aquí no se valida
  contra Shopify. Se detecta al crear la orden, porque `process_shipment` ya
  informa los SKU no encontrados.
