# App `quotes`

Cotizaciones: las draft orders de Shopify y su PDF, migradas tal cual de
`pamo_web` (`quote_print/`, ruta `/quoteprint/`) el 2026-10-22. No tiene
modelos: lee Shopify en vivo. No crea ni edita cotizaciones; eso sigue en el
admin de Shopify.

## Código

| Pieza | Ubicación | Qué hace |
| --- | --- | --- |
| Listado | `quotes/functions/list_quotes.py` | Las 250 más recientes (`LIST_SIZE`) o las que coinciden con `search`, con `list_draft_orders`. Arma cada fila y su link público. |
| Links | `quotes/functions/quote_links.py` | `quote_link(id)` / `quote_id_from_link(token)`: link firmado al PDF (ver "Link para el cliente"). |
| PDF | `quotes/functions/quote_pdf.py` | `quote_pdf(draft) → (nombre, bytes)`, con reportlab. |
| Imágenes del PDF | `quotes/pdf_assets/` | Fondos de hoja, botones y banners copiados de `pamo_web/static/images/cotizacion_img/`. Se leen del disco; no son estáticos servidos. |
| API | `quotes/apis.py` | `QUOTES_ROLES = ["Admin", "Operaciones"]`. Contrato en [`../contracts/API.md`](../contracts/API.md). |

Las consultas a Shopify son `list_draft_orders` y `get_draft_order`
(`integrations/shopify/`; ver [`integrations.md`](integrations.md)).

## Reglas copiadas de `pamo_web`

- **Listado**:
  - cliente = nombre y apellido en tipo título;
  - vendedor = la **primera** etiqueta del draft order;
  - NIT = lo que va antes del `-` en la empresa (`company`) de la
    dirección principal, hasta 24 caracteres (la misma que usa
    `customers`);
  - fecha = creación, en día de Colombia.
- **PDF**: es una copia de `pdf_generator.py` con las mismas coordenadas.
  - Hojas: portada, "¿Por qué Pamo?" y productos (7 por hoja). La primera
    de productos lleva los datos de la empresa y del cliente; los totales
    pasan a una hoja nueva si no caben sobre el pie.
  - Precios: Shopify los guarda con IVA. Precio unitario y total bruto van
    divididos por 1,19, y el subtotal sale del total de la cotización.
  - Descuento: ver "Diferencias con `pamo_web`".
  - Fecha = última edición (día de Colombia). Vencimiento = fecha + 10
    días.
  - Botones: "Paga en línea" (`invoiceUrl`), certificado bancario y RUT
    (PDF fijos en el CDN de Shopify), WhatsApp con mensaje que cita el
    número, y teléfono (`SALES_PHONE`).
  - Las fotos se bajan con `download_image` (solo CDN de Shopify). Si una
    falla, la fila sale sin foto.

## Diferencias con `pamo_web`, a propósito

- **NIT del PDF**: se lee del metafield `custom.numero_documento_identificaci_n`
  por clave y se toma el número entre corchetes (`[900382733]`). `pamo_web`
  vaciaba los dos primeros metafields antes de buscarlo, y el PDF salía sin
  NIT cuando quedaba en esas posiciones. Verificado el 2026-10-22: D7285 y
  D7265 salían sin NIT; ahora salen con él.
- **Descuento** (pedido el 2026-10-22): sale el descuento de toda la
  cotización (`appliedDiscount`) de Shopify.
  - Etiqueta: "Descuento (7%)", en una línea. Si es porcentaje, el valor de
    Shopify. Si es de valor fijo, el monto sobre el precio de lista con IVA
    (suma de líneas), redondeado (D7238: 106.904 / 1.527.200 = 7%). El
    nombre que le pone el vendedor no sale: quedaba muy cargado.
  - Monto: el de Shopify, **con IVA** (D7238: $106.904). `pamo_web` lo
    recalculaba sin IVA (bruto − subtotal: $89.835).
  - Por eso total bruto − descuento ya no es igual al subtotal, que va sin
    IVA. Así lo pidió el negocio.
  - Los descuentos por producto no se muestran: ninguna de las 250
    cotizaciones revisadas el 2026-10-22 los usaba.
- **SKU**: el de la línea (`lineItem.sku`), no el de la primera variante del
  producto.
- **Fotos**: imagen de la variante o, si no tiene, la principal del
  producto.
- **Botón de WhatsApp**: el mismo dibujo, pasado de SVG a PNG
  (`pdf_assets/wp.png`). Así no hace falta svglib.
- **Sin copia local ni "Actualizar"**: `pamo_web` guardaba las cotizaciones
  en la tabla `Quote` solo para paginar. Aquí se lee Shopify en cada
  consulta, y la búsqueda (texto libre de Shopify: número sin `#`, cliente)
  encuentra las más viejas.
- **No se migró** lo de Siigo de `quote_print` (sincronizar clientes, ver
  cliente): en `pamo_web` ya no se usaba.

Costo medido el 2026-10-22: 24 puntos el listado con 250 y 61 una cotización
completa.

## Link para el cliente

El cliente no tiene usuario, así que el PDF se le manda por link público
firmado (patrón de [`../patterns/AUTHORIZATION.md`](../patterns/AUTHORIZATION.md),
igual que `orders/functions/label_links.py`):

- `signing.dumps({"q": <id>}, salt="quotes.pdf-link")`; la URL es
  `BACKEND_PUBLIC_URL/api/quotes/pdf/<token>/`.
- Sin `BACKEND_PUBLIC_URL`, `pdf_link` viene vacío.
- Vence a los 90 días (`LINK_MAX_AGE`); la cotización vale 10. Si vence, el
  vendedor copia otro desde el listado.
- No lleva huella del contenido: el PDF se arma al abrirlo, con lo que tenga
  Shopify en ese momento. Si se borra la cotización, el link da `404`.
- En `pamo_web` el PDF se abría solo con el id (`/quoteprint/<id>/pdf/`), sin
  firma. Los links que ya tienen los clientes funcionan mientras `pamo_web`
  siga arriba.

## Configuración

- `SALES_PHONE`: número de ventas, con indicativo y sin `+`.
- `BACKEND_PUBLIC_URL`: ya existía (links de guías).

Las dos se leen en `config/constants.py`.

## Pendiente

- Definir en Railway `SALES_PHONE`; sin ella, los botones de WhatsApp y
  teléfono quedan sin número.
- Fin de convivencia: decidir con ventas desde cuándo se mandan solo los
  links nuevos, antes de apagar `/quoteprint/` en `pamo_web`.

## Pruebas

- `quotes/tests.py`:
  - PDF: hojas, cliente, fechas, totales, descuento (porcentaje y monto de
    Shopify, también el fijo pasado a porcentaje), 7 productos por hoja,
    NIT del metafield y una foto que falla;
  - links: ida y vuelta, y sin URL pública;
  - filas del listado;
  - API: rechazo por rol, descarga, `404`, link público sin sesión, link
    alterado (`404`), vencido (`410`) y de una cotización borrada (`404`).
- `integrations/shopify/tests.py`: normalización de `list_draft_orders` y
  `get_draft_order`.

Para correrlas:

```
DATABASE_URL= python manage.py test quotes integrations.shopify
```
