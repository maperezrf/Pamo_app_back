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
y su expiración; es estado de conexión, no información de clientes o
facturas.

## Proveedores

- `shopify/`: cliente GraphQL, funciones y catálogo `QUERIES.md`. Incluye
  funciones de cliente (`create_customer`, `create_customer_address`,
  `update_customer_address`, `list_customers_page`) que consume la app
  `customers`, y verificación de firma de webhook
  (`ShopifyClient.verify_webhook_signature`).
- `falabella/`: cliente REST firmado y funciones de pedidos.
- `sodimac/`: cliente REST y funciones de pedidos.
- `envia/`: cliente REST, cotización y validación de payload logístico.
- `siigo/`: cliente REST, token cacheado y funciones de clientes/facturas.
- `whatsapp/`: cliente REST (WhatsApp Cloud API de Meta) y funciones de
  envío (texto, documento, plantilla, botones) y de parseo de webhook.

Cada proveedor mantiene sus pruebas cerca de su código. Las pruebas deben
simular la red y verificar autenticación, errores y normalización sin usar
credenciales reales.
