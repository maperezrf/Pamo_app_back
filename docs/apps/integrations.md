# App `integrations`

`integrations/` centraliza clientes y adaptadores de proveedores. Es una app
técnica compartida, no la dueña de pedidos, facturas ni otros datos de
negocio. Ver también [arquitectura de integraciones](../architecture/INTEGRATIONS.md).

## Uso

Una app de negocio consume una función de
`integrations/<provider>/functions/`. Esa función valida la operación y usa
el cliente genérico del proveedor. No se debe llamar al transporte desde una
vista ni duplicar autenticación en una app de negocio.

## Estado persistido

`integrations/models.py` reexporta los modelos técnicos de proveedores para
que Django los detecte. Actualmente `SiigoToken` conserva el token cacheado
y su expiración, y `MercadoLibreToken` el access/refresh token OAuth2 y el
seller id; es estado de conexión, no información de clientes o facturas.

## Proveedores

- `shopify/`: cliente GraphQL, funciones y catálogo `QUERIES.md`. Incluye
  funciones de cliente (`create_customer`, `create_customer_address`,
  `update_customer_address`, `list_customers_page`) que consume la app
  `customers`, verificación de firma de webhook
  (`ShopifyClient.verify_webhook_signature`), y
  `get_variant_inventory_by_sku` (variante + unidades disponibles por
  bodega) que consume `orders`.
  - `get_variant_images_by_skus(skus) → {sku: url}`: imagen de la variante
    o, si no tiene (lo normal en la tienda), la principal del producto.
    Reutiliza la búsqueda por lotes (`search_variants_by_skus`). La consume
    el PDF de despachos de `orders`. `download_image(url, width=240)` baja
    la imagen ya reducida, solo de `cdn.shopify.com` (sin redirecciones,
    máximo 2 MB).
  - `get_variants_by_skus(skus) → {sku: variant_id}` consulta por lotes,
    sin inventario. Hace bloques de 50 SKU (`sku:"A" OR sku:"B"`, con
    comillas y barras escapadas) y recorre `pageInfo`. Solo acepta
    coincidencias exactas y propaga el error de un bloque. La consume la
    carga de SKU de `products`. Antes de subir `BATCH_SIZE`, confirmar
    contra la tienda real el largo máximo de la búsqueda.
  - `list_orders_page(*, first, after=None, query="", sort_key="CREATED_AT", reverse=True)
    → {"orders", "has_next_page", "end_cursor"}`: una página de pedidos
    normalizados (cliente, hasta 30 líneas y `line_items_truncated`).
    Recibe la `query` de Shopify ya armada; no conoce marketplaces.
  - `get_order(order_id) → pedido | None`: un pedido con **todas** sus
    líneas; `None` si no existe o se borró.
  - Las dos normalizan con `normalize_order` (fragmento `ORDER_FIELDS`),
    así que devuelven la misma forma. Las consume la copia local de
    pedidos de `orders`.
  - `fulfillment_orders` del pedido normalizado: las bodegas desde donde
    sale según Shopify (`{id, status, location_id, location_name, items:
    [{sku, name, quantity}], tracking: {number, company, url}}`). Se
    descartan los `CANCELLED` y los que vienen sin productos (un
    movimiento de bodega deja uno `CLOSED` vacío). `tracking` es la guía
    del último fulfillment de ese fulfillment order con número. Costo
    medido de `LIST_ORDERS_PAGE` con esto: 835 puntos con `first: 50`
    (2026-10-15).
  - `get_fulfillment_order_order_id(id) → str`: pedido (sin `gid://`) de
    un fulfillment order; `""` si no existe. Lo usa el webhook de pedidos
    con los avisos `fulfillment_orders/*`.
  - Los webhooks de Shopify se suscriben desde el admin, no por API (ver
    [`orders.md`](orders.md)).
- `falabella/`: cliente REST firmado y funciones de pedidos.
- `mercadolibre/`: cliente OAuth2 con token en BD (`MercadoLibreToken`,
  refresh token rotativo renovado bajo bloqueo de fila) y funciones de
  lectura `get_order`, `get_billing_info`, `get_shipment` y `get_pack`
  (verificadas contra pedidos reales el 2026-09-24). `get_billing_info`
  requiere el permiso de facturación
  habilitado en la app de Mercado Libre (sin él, 403). Mercado Libre no
  entrega email ni teléfono del comprador. La cuenta se conecta desde el navegador con
  `GET /api/integrations/mercadolibre/connect/` → `callback/` (rol
  `Admin`, `state` de un solo uso en la sesión), en
  `mercadolibre/apis.py`; rutas en `integrations/urls.py`. Flujo de
  pedidos en [`orders.md`](orders.md) ("Mercado Libre").
- `madecentro/`: API de Shipturtle (plataforma del marketplace
  Madecentro), con token Bearer fijo que no vence.
  - Shipturtle entrega un token por dominio. `MadecentroClient(token)`
    recibe el que corresponde; hoy solo se usa `MADECENTRO_ORDERS_TOKEN`.
  - `get_order(order_id)`: pedido normalizado (id, número, estado de
    pago, cancelado, comprador e ítems con SKU, cantidad y precio). No hay
    documento del comprador porque Shipturtle no lo entrega.
  - `list_orders(start_date, end_date, page=, limit=)`
    (`/all-orders/fetchData`, fechas `M/D/YYYY`, inclusive): devuelve
    `{"orders": [{order_id, order_number, financial_status}], "count"}`. El
    listado de Shipturtle es resumido (sus líneas no traen SKU ni precio),
    así que solo sirve para descubrir pedidos.
- `sodimac/`: cliente REST y funciones de pedidos: `get_orders(tipo)` (lee
  la cola, que es destructiva) y `reinject_order(oc)` (devuelve una OC a
  la cola). Los consume `orders.sync_sodimac`.
- `pamo_web/`: **temporal**. Cliente hacia el sistema anterior (header
  `X-Bot-Token`, `PAMO_WEB_BASE_URL` / `PAMO_WEB_BOT_TOKEN`) y
  `run_sodimac_invoicing()`, que llama a `POST /pamo_bots/sodimac/invoices`
  para que `pamo_web` facture sus OC de Sodimac antiguas. Se quita al
  terminar la convivencia (ver "Sodimac" en [`orders.md`](orders.md)).
- `envia/`: API de Shipping de Envía: cliente REST, validación del payload,
  cotización (`quote`), generación de guía (`create_label`, **cobra**) y
  descarga del PDF (`download_label`).
- `envia_fulfillment/`: cliente REST de Envía Fulfillment (otra API que
  `envia/`), lecturas de bodegas y órdenes, y creación de órdenes con la
  guía adjunta. Escrituras apagadas por defecto. En construcción.
- `siigo/`: cliente REST, token cacheado y funciones de clientes/facturas.
- `gmail/`: cliente OAuth de la API de Gmail, función `send_message` y el
  backend de correo `GmailApiEmailBackend` (envío por HTTPS; ver
  `architecture/INTEGRATIONS.md`). Comando `gmail_authorize` para obtener
  el refresh token.
- `sodimac_rpa/`: cliente del RPA de guías de Sodimac (servicio propio en
  Railway) y `trigger_labels`; el webhook y la consulta de bodega viven en
  `orders`.
- `zernio/`: WhatsApp a través de Zernio (reemplazó a la WhatsApp Cloud
  API de Meta el 2026-10-16). Cliente REST con API key y funciones
  `send_whatsapp_template` (plantilla aprobada a un número, con PDF
  opcional en el encabezado por URL), `upload_media` (sube el PDF y
  devuelve una URL pública temporal) y `list_whatsapp_templates`. Las
  consume el canal `whatsapp` de los avisos de despacho de `orders`.

Cada proveedor mantiene sus pruebas cerca de su código. Las pruebas deben
simular la red y verificar autenticación, errores y normalización sin usar
credenciales reales.
