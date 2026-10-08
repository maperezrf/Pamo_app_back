# Arquitectura de integraciones

Las conexiones con terceros viven bajo `integrations/<provider>/`. Cada
proveedor incorpora solo los archivos necesarios para una operación real.

## Separación obligatoria

- `client.py`: autenticación y transporte genérico. No conoce operaciones de
  negocio como “crear factura” o “obtener pedidos”.
- `functions/`: operaciones con nombre de negocio, validación y adaptación
  de respuestas. Estas llaman al cliente genérico.
- `models.py`: solo estado de conexión, por ejemplo token cacheado,
  checkpoints o eventos técnicos. Nunca datos de negocio.
- `queries.py` y `QUERIES.md`: consultas GraphQL reutilizables cuando el
  proveedor las use.
- `tests.py`: pruebas sin red, usando mocks de transporte.

Las credenciales, URL y puertas de configuración se declaran únicamente por
nombre en `config/constants.py` y se entregan mediante entorno. Ni esta
documentación ni las respuestas de la API muestran sus valores.

## Proveedores presentes

| Proveedor | Transporte principal | Estado técnico conocido |
| --- | --- | --- |
| Shopify | GraphQL | `ShopifyClient.request_graphql()` y catálogo de consultas. También `ShopifyClient.verify_webhook_signature()` (webhooks entrantes, ver `docs/patterns/PROVIDER_WEBHOOKS.md`) y funciones de cliente (`create_customer`, `create_customer_address`, `update_customer_address`, `list_customers_page`) que consume la app `customers`. `get_variant_inventory_by_sku` devuelve la variante con unidades disponibles por bodega (`Location`), usada por `orders` para elegir bodega de despacho. `list_locations` lista las bodegas (también inactivas) para el registro de bodegas de despacho. |
| Falabella | REST firmada HMAC | `FalabellaClient.request()` firma y envía acciones. `get_shipping_document(order_item_ids)` descarga el PDF de la guía del paquete (`GetDocument`, `shippingParcel`; solo lectura, requiere `PackageId`). `get_package_items(order_id)` da los ítems por unidad con `PackageId` y `TrackingCode`, para pedir esa guía. |
| Mercado Libre | REST OAuth2 (Bearer) | `MercadoLibreClient.get()` usa el token de `MercadoLibreToken` y lo renueva antes de vencer o ante un 401 (un reintento). El refresh token es de un solo uso y rota: la renovación bloquea la fila y guarda el nuevo antes de usarlo. Sin fila o con `invalid_grant` → `MercadoLibreAuthError` (volver a conectar en `GET /api/integrations/mercadolibre/connect/`). `get_bytes()` para respuestas no JSON: `get_shipment_label(shipment_id)` descarga el PDF de la etiqueta; `is_label_available(shipment)` dice si el envío está en un estado imprimible (descargar en `ready_to_print` lo marca `printed`). |
| Madecentro | REST Bearer fijo (API de Shipturtle) | `MadecentroClient(token).get()`: un token por dominio (pedidos, productos) que no vence; se bloquean redirecciones y el error no expone el token. Solo lectura de pedidos (`get_order` normalizado y `list_orders` resumido), que consume la rutina `orders.import_madecentro`; los webhooks se capturan en `orders`. |
| Sodimac | REST con subscription key | `SodimacClient.post()` envía solicitudes JSON. |
| Envía | REST Bearer | `EnviaClient` valida respuestas, redirecciones y tamaño máximo. |
| Envía Fulfillment | REST Bearer (`apifulfillment.envia.com`, otra API y otro token que Envía Shipping) | `EnviaFulfillmentClient` resuelve `{company_id}`, manda el header `timezone`, bloquea redirecciones y respuestas grandes, y **bloquea toda escritura** (no GET) sin `ENVIA_FULFILLMENT_WRITES_ENABLED`. Funciones: `list_warehouses`, `find_order`, `get_order`, `create_order` (con la guía del canal en `shipments`), `add_order_tracking`, `list_shop_products`. En construcción: ver [`../implementations-plans/envia-fulfillment-dispatch.md`](../implementations-plans/envia-fulfillment-dispatch.md). |
| Siigo | REST Bearer con token cacheado | `SiigoClient.request()` reutiliza `SiigoToken` vigente. |
| WhatsApp | REST Bearer (WhatsApp Cloud API, Meta) | `WhatsAppClient.send_message()`/`upload_media()` son transporte puro; cada tipo de mensaje (texto, documento, plantilla, botones) tiene su propia función en `functions/`, sin depender entre sí. |

**Correo**: no hay proveedor propio en `integrations/`. Los avisos de despacho usan `django.core.mail` por SMTP, configurado con `EMAIL_*` y `DEFAULT_FROM_EMAIL` en `config/constants.py`; cualquier proveedor SMTP sirve sin cambiar código.

Antes de agregar una operación, revisar el cliente y las funciones existentes
del proveedor. Si usa GraphQL, revisar primero
[`integrations/shopify/QUERIES.md`](../../integrations/shopify/QUERIES.md) o
el catálogo equivalente antes de definir otra consulta.

No realizar escrituras reales en un proveedor sin una autorización explícita
del requerimiento y sin una prueba que diferencie la preparación local del
efecto externo.
