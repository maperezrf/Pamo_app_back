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
  `mercadolibre/apis.py`; rutas en `integrations/urls.py`. Plan:
  [`../implementations-plans/mercadolibre-orders-import.md`](../implementations-plans/mercadolibre-orders-import.md).
- `madecentro/`: API de Shipturtle (plataforma del marketplace
  Madecentro), con token Bearer fijo que no vence.
  - Shipturtle entrega un token por dominio. `MadecentroClient(token)`
    recibe el que corresponde; hoy solo se usa `MADECENTRO_ORDERS_TOKEN`.
  - Funciones de lectura `get_order(order_id)` y
    `list_orders(start_date, end_date, page=, limit=)`
    (`/all-orders/fetchData`, fechas `M/D/YYYY`). Devuelven el JSON sin
    normalizar hasta conocer los campos reales.
  - Plan:
    [`../implementations-plans/madecentro-orders-import.md`](../implementations-plans/madecentro-orders-import.md).
- `sodimac/`: cliente REST y funciones de pedidos.
- `envia/`: cliente REST, cotización y validación de payload logístico.
- `siigo/`: cliente REST, token cacheado y funciones de clientes/facturas.
- `whatsapp/`: cliente REST (WhatsApp Cloud API de Meta) y funciones de
  envío (texto, documento, plantilla, botones) y de parseo de webhook.

Cada proveedor mantiene sus pruebas cerca de su código. Las pruebas deben
simular la red y verificar autenticación, errores y normalización sin usar
credenciales reales.
