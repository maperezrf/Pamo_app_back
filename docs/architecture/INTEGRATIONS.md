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
| Shopify | GraphQL | `ShopifyClient.request_graphql()` y catálogo de consultas. También `ShopifyClient.verify_webhook_signature()` (webhooks entrantes, ver `docs/patterns/PROVIDER_WEBHOOKS.md`) y funciones de cliente (`create_customer`, `create_customer_address`, `update_customer_address`, `list_customers_page`) que consume la app `customers`. |
| Falabella | REST firmada HMAC | `FalabellaClient.request()` firma y envía acciones. |
| Sodimac | REST con subscription key | `SodimacClient.post()` envía solicitudes JSON. |
| Envía | REST Bearer | `EnviaClient` valida respuestas, redirecciones y tamaño máximo. |
| Siigo | REST Bearer con token cacheado | `SiigoClient.request()` reutiliza `SiigoToken` vigente. |
| WhatsApp | REST Bearer (WhatsApp Cloud API, Meta) | `WhatsAppClient.send_message()`/`upload_media()` son transporte puro; cada tipo de mensaje (texto, documento, plantilla, botones) tiene su propia función en `functions/`, sin depender entre sí. |

Antes de agregar una operación, revisar el cliente y las funciones existentes
del proveedor. Si usa GraphQL, revisar primero
[`integrations/shopify/QUERIES.md`](../../integrations/shopify/QUERIES.md) o
el catálogo equivalente antes de definir otra consulta.

No realizar escrituras reales en un proveedor sin una autorización explícita
del requerimiento y sin una prueba que diferencie la preparación local del
efecto externo.
