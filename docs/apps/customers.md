# App `customers`

`customers/` mantiene un directorio local sincronizado con los clientes de
Shopify. Existe porque la API de Shopify **no permite buscar un cliente por
`company`** (donde vive la cédula/NIT en este proyecto) — ver
[`../implementations-plans/shopify-customers-directory.md`](../implementations-plans/shopify-customers-directory.md)
para la evidencia completa. Cualquier canal que necesite resolver "¿ya
existe este cliente?" por cédula consulta esta tabla local, nunca Shopify en
vivo. Falabella (`orders`) **ya no la consume**: sus pedidos van a un
cliente fijo de Shopify (ver
[`../implementations-plans/falabella-fixed-customer.md`](../implementations-plans/falabella-fixed-customer.md)).

## Capacidades

- `ShopifyCustomer`: `shopify_id` (único, sin prefijo `gid://`),
  `identification` (de `defaultAddress.company`, indexado),
  `first_name`/`last_name`, `email`/`phone` (indexados),
  `default_address_id` (tal cual lo entrega Shopify, sin recortar prefijo —
  su formato exacto no está confirmado), `shopify_updated_at`, `synced_at`.
- `functions/find_by_identification.py`: lookup **local** por cédula (no
  toca Shopify). Si hay más de un match (dato posiblemente sucio de antes
  de este proyecto), devuelve el más reciente.
- `functions/set_identification.py`: escribe/actualiza la cédula de un
  cliente existente en Shopify — decide sola entre crear la primera
  dirección o actualizar la que ya tiene.
- `functions/upsert_from_shopify_customer.py`: punto único de "cómo se
  guarda un cliente" localmente; lo comparten el webhook y la
  reconciliación.
- `functions/reconcile_from_shopify.py`: proceso de `orchestrator`
  (`customers.reconcile_shopify`) que pagina TODOS los clientes de Shopify
  y hace upsert de cada uno. Programado a las 2am. Verificado en vivo el
  2026-09-15: **19.605 clientes reales sincronizados en 157.6s**, de los
  cuales 5.314 ya tenían una cédula (6-10 dígitos) en `company` antes de
  este proyecto — la primera corrida de este proceso ya hizo de backfill,
  sin necesidad de un script aparte.
- `webhooks.py` / `functions/parse_customer_webhook.py` /
  `functions/process_customer_webhook.py`: recepción en tiempo real de
  `customers/create`/`customers/update`, siguiendo
  [`../patterns/PROVIDER_WEBHOOKS.md`](../patterns/PROVIDER_WEBHOOKS.md).
  Proceso `customers.process_webhook` (`allow_concurrent=True`).

## Por qué es una app aparte

No es transporte de proveedor (`integrations/shopify` solo hace
adaptación, sin persistencia) ni es específica de un canal de pedidos
(`orders` la va a consumir, pero no la va a poseer). Confirmado contra
`docs/architecture/APP_BOUNDARIES.md` antes de crearla.

## Rutas actuales

`POST /api/customers/webhooks/shopify/customer/` — público (`AllowAny`),
autenticado por firma HMAC (`X-Shopify-Hmac-Sha256`), no por sesión ni rol.
Ver `docs/patterns/PROVIDER_WEBHOOKS.md`.

## Puntos abiertos

- Suscribir el webhook en Shopify (`webhookSubscriptionCreate`) requiere
  una URL pública HTTPS — pendiente, no disponible en desarrollo local. El
  endpoint y la verificación de firma están construidos y probados con
  payloads simulados, pero no se probó la entrega real de un webhook.
- La forma exacta del payload REST de `customers/create`/`customers/update`
  no se verificó contra un evento real de Shopify — `parse_customer_webhook.py`
  asume la forma estándar documentada por Shopify.
- `SHOPIFY_WEBHOOK_SECRET` (`config/constants.py`) está declarado pero
  vacío en `.env` — pendiente de conseguir en el Shopify Admin de la
  tienda.

## Pruebas

`customers/tests.py`: lookup local, upsert (incluida la regla de no pisar
`default_address_id` con datos parciales del webhook), `set_identification`
(las dos ramas: crear vs actualizar dirección, con los clientes de Shopify
mockeados), reconciliación paginada (con cancelación), parseo de webhook, y
el endpoint completo (firma válida/inválida/ausente). Sin red real — misma
convención que el resto de `integrations/`.
