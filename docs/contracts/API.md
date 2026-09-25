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
| `GET /api/integrations/mercadolibre/connect/` | Rol `Admin` | — (navegación del navegador, no XHR) | `302` a la autorización de Mercado Libre; `503` si faltan credenciales. |
| `GET /api/integrations/mercadolibre/callback/` | Rol `Admin` (misma sesión que `connect/`) | Query `code`, `state` o `error` (los pone Mercado Libre). | `connected: true`, `seller_id`; `400` si `state` inválido/vencido/reusado, el vendedor rechazó o el código fue rechazado; `502` si Mercado Libre falla. |
| `GET /api/products/` | Rol `Admin` | Query `search` (SKU de Pamo o de marketplace), `page`. | Paginado (`count`, `next`, `previous`, `results`); cada producto: `sku`, `name`, `is_kit`, `marketplace_skus` (`marketplace`, `sku`, `ean`), `components` (`sku`, `quantity`). |
| `GET /api/products/equivalences/` | Rol `Admin` | — | `columns` (`pamo_sku`, `<marketplace>_sku`, `<marketplace>_ean`) y `rows`; varios SKU de un canal repiten la fila. |
| `POST /api/products/equivalences/` | Rol `Admin` | `rows`: lista de objetos con las columnas anteriores (`pamo_sku` obligatorio, cualquier subconjunto del resto). | `created`, `updated`, `products_created`, `moved` (`marketplace`, `sku`, `from`, `to`), `errors` (`row` 1-based, `error`); `400` si `rows` no es una lista de objetos o hay columnas desconocidas (`unknown_columns`). |
| `GET /api/products/kits/` | Rol `Admin` | — | `columns` (`kit_sku`, `component_sku`, `quantity`) y `rows` (una por componente). |
| `POST /api/products/kits/` | Rol `Admin` | `rows` con las columnas anteriores; cada kit que viene reemplaza completo sus componentes. | `created`, `updated`, `errors`; `400` igual que equivalencias. |

## Errores de acceso relevantes

- Sin sesión en rutas protegidas: `403` con la configuración actual de
  autenticación por sesión de DRF.
- Credencial Google ausente o inválida: `400` y `authorized: false`.
- Correo no verificado o no permitido: `403` y `authorized: false`.

Un cambio de esta tabla exige coordinar el consumidor del frontend antes de
considerarlo terminado.
