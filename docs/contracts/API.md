# Contrato HTTP publicado

Base actual: `/api/`. Las rutas de autenticación viven bajo `/api/auth/`.
Este documento describe solo el contrato implementado; no contiene secretos
ni datos de entorno.

| Método y ruta | Acceso | Entrada | Respuesta principal |
| --- | --- | --- | --- |
| `GET /api/auth/csrf/` | Público | — | `detail`, `csrftoken`. |
| `POST /api/auth/google/` | Público | `credential` de Google. | `authorized` y, si procede, `user`. |
| `GET /api/auth/me/` | Sesión | — | `email`, `username`. |
| `POST /api/auth/logout/` | Sesión | — | `detail: logged_out`. |
| `GET /api/auth/menu/` | Sesión | — | Árbol de áreas filtrado por roles. |
| `GET /api/auth/ping-admin/` | Rol `Admin` | — | `is_admin: true`. |
| `POST /api/orders/webhooks/mercadolibre/` | Público (`AllowAny`), lo llama Mercado Libre; sin firma, se valida tópico, app y cuenta | Notificación `orders_v2` (`resource`, `topic`, `user_id`, `application_id`). | Siempre `200`, vacío; solo una notificación válida lanza `orders.process_mercadolibre_notification`. |
| `GET /api/orders/` | Rol `Admin` u `Operaciones` | Query opcional: `marketplace` (`falabella`, `mercadolibre`, `madecentro`, `sodimac`, `shopify` = sin marketplace), `date_from` / `date_to` (`YYYY-MM-DD`, días de Colombia, inclusive, sobre la creación en Shopify), `search` (número de Shopify o del marketplace), `page`, `page_size` (1–50, por defecto 20). | Solo lee la copia local (nunca Shopify). `count`, `next`, `previous` y `orders`: pedidos más reciente primero, cada uno con `shopify_order_id`, `shopify_order_name`, `created_at`, `cancelled_at` (`null` si no está cancelado), `financial_status`, `fulfillment_status`, `marketplace`, `marketplace_order_numbers`, `customer` (comprador real: `identification_type`, `identification`, `first_name`, `last_name`, `email`, `phone`, `city`, `region`, `address`), `fulfillment` (bodega de despacho tal cual en la base: `status` `asignada`/`novedad`/`resuelta_manual` o vacío si no se evaluó, `location_id`, `location_name`, `note` con el motivo de la novedad; todo vacío en pedidos sin fila local), `items` (`sku`, `name`, `quantity`, `unit_price`, `line_total`), `total`, `currency`. `orders_not_created`: pedidos de marketplace sin orden en Shopify (`id`, `marketplace`, `marketplace_order_number`, `shipment_id`, `created_at`, `updated_at`, `status`, `error`, `customer`, `fulfillment`, `items` con el SKU del marketplace, `total`), completa en cada página, hasta 200, y `orders_not_created_count`. `last_synced_at`: última reconciliación con Shopify (`null` si nunca corrió). Cada pedido de `orders` trae `dispatch` (desde 2026-10-07): `null` si el proceso de despacho no lo ha tomado, o `{status` (`esperando_guia`, `listo`, `notificado`, `error`, `manual`, `cancelado`), `location_name`, `note` (motivo legible), `tracking_number`, `notifications` (`[{channel` `api`/`email`/`whatsapp`, `status` `pendiente`/`procesando`/`enviado`/`error`, `recipient`, `sent_at`, `error}]`), `updated_at}`. Fechas en hora de Colombia; importes como string con 2 decimales. `400` por parámetro inválido; `404` si la página no existe. |
| `POST /api/orders/webhooks/shopify/` | Público (`AllowAny`), lo llama Shopify; firma HMAC `X-Shopify-Hmac-Sha256` sobre el body crudo | Aviso `orders/create`, `orders/updated` u `orders/delete` (`X-Shopify-Topic`); solo se usa el `id` del pedido. | `403` si la firma no es válida. Si no, siempre `200` vacío; solo un tema conocido con `id` lanza `orders.process_shopify_order_webhook`. |
| `GET /api/integrations/mercadolibre/connect/` | Rol `Admin` | — (navegación del navegador, no XHR) | `302` a la autorización de Mercado Libre; `503` si faltan credenciales. |
| `GET /api/integrations/mercadolibre/callback/` | Rol `Admin` (misma sesión que `connect/`) | Query `code`, `state` o `error` (los pone Mercado Libre). | `connected: true`, `seller_id`; `400` si `state` inválido/vencido/reusado, el vendedor rechazó o el código fue rechazado; `502` si Mercado Libre falla. |
| `GET /api/products/` | Rol `Admin` | Query `search` (SKU de Pamo o de marketplace), `page`. | Paginado (`count`, `next`, `previous`, `results`); cada producto: `sku`, `name`, `is_kit`, `marketplace_skus` (`marketplace`, `sku`, `ean`), `components` (`sku`, `quantity`). |
| `GET /api/products/equivalences/` | Rol `Admin` | — | `columns` (`pamo_sku`, `<marketplace>_sku`, `<marketplace>_ean`) y `rows`; varios SKU de un canal repiten la fila. |
| `POST /api/products/equivalences/` | Rol `Admin` | `rows`: lista de objetos con las columnas anteriores (`pamo_sku` obligatorio, cualquier subconjunto del resto). | `created`, `updated`, `products_created`, `moved` (`marketplace`, `sku`, `from`, `to`), `errors` (`row` 1-based, `error`); `400` si `rows` no es una lista de objetos o hay columnas desconocidas (`unknown_columns`). |
| `GET /api/products/kits/` | Rol `Admin` | — | `columns` (`kit_sku`, `component_sku`, `quantity`) y `rows` (una por componente). |
| `POST /api/products/kits/` | Rol `Admin` | `rows` con las columnas anteriores; cada kit que viene reemplaza completo sus componentes. | `created`, `updated`, `errors`; `400` igual que equivalencias. |
| `POST /api/products/sku-uploads/` | Rol `Admin` (`SKU_UPLOAD_ROLES`) | `marketplace` (`falabella`, `mercadolibre`, `madecentro`, `sodimac`) y `rows`: objetos con `sku_pamo`, `sku_marketplace`, `ean` opcional y cualquier otra columna (se conserva). Máximo 2000. | `202` `id`, `execution_id`. La carga corre en segundo plano. `400` si el marketplace no es válido, `rows` está vacío o supera el tope, o una fila no trae `sku_pamo` o `sku_marketplace`. |
| `GET /api/products/sku-uploads/<id>/` | Rol `Admin` | — (polling) | `id`, `marketplace`, `uploaded_by` (email), `created_at`, `finished_at`, `status` (estado del orquestador: `PENDIENTE`, `EN_COLA`, `EJECUTANDO`, `COMPLETADO`, `ERROR`, `CANCELADO`, `INTERRUMPIDO`...), `progress_percent`, `current_step`, `error_message`, `summary` (`total`, `por_codigo`, `por_caso`) y `rows` (filas de entrada más `resultado` y `resultado_codigo` `ok`/`alert`/`error`; `null` hasta terminar). `404` si no existe. |
| `GET /api/products/sku-uploads/` | Rol `Admin` | Query `page`. | Paginado de 20 (`count`, `next`, `previous`, `results`), más reciente primero; los mismos campos del detalle sin `rows`. Todas las cargas, no solo las propias. |

## Errores de acceso relevantes

- Sin sesión en rutas protegidas: `403` con la configuración actual de
  autenticación por sesión de DRF.
- Credencial Google ausente o inválida: `400` y `authorized: false`.
- Correo no verificado o no permitido: `403` y `authorized: false`.

Un cambio de esta tabla exige coordinar el consumidor del frontend antes de
considerarlo terminado.
