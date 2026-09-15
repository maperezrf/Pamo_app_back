# Catálogo de consultas y mutaciones GraphQL — Shopify

Antes de escribir una consulta o mutación nueva en
`integrations/shopify/queries.py`, revisar esta tabla primero: si ya
existe una que trae el campo que hace falta, se reutiliza (agregándole el
campo si aplica) en vez de duplicarla.

| Nombre | Tipo | Usada en | Qué trae/hace | Agregada |
|---|---|---|---|---|
| `GET_VARIANT_BY_SKU` | query | `functions/get_variant_by_sku.py` | id de variante, SKU e id/título del producto, a partir de un SKU (con verificación de coincidencia exacta en la función que la usa) | 2026-09-10 |
| `CREATE_ORDER` | mutation | `functions/create_order.py` | crea un pedido (`orderCreate`): line items (variante, cantidad, precio), cliente asociado, estado financiero, nota, tags. Campos de `OrderCreateOrderInput`/`OrderCreateLineItemInput` verificados por introspección contra el schema real (API 2024-07) el 2026-09-10, no adivinados. | 2026-09-10 |
| `CREATE_CUSTOMER` | mutation | `functions/create_customer.py` | crea un cliente (`customerCreate`): email, phone, nombre. `CustomerInput` verificado por introspección el 2026-09-15 -- **no tiene ningún campo de dirección**, por eso la cédula se escribe aparte. | 2026-09-15 |
| `CREATE_CUSTOMER_ADDRESS` | mutation | `functions/create_customer_address.py` | agrega la primera dirección de un cliente (`customerAddressCreate`), con `company` (cédula) y `setAsDefault`. `MailingAddressInput` verificado por introspección el 2026-09-15. | 2026-09-15 |
| `UPDATE_CUSTOMER_ADDRESS` | mutation | `functions/update_customer_address.py` | actualiza una dirección existente de un cliente (`customerAddressUpdate`), mismo `MailingAddressInput`. Para clientes que ya tienen `addressId` conocido. | 2026-09-15 |
| `LIST_CUSTOMERS_PAGE` | query | `functions/list_customers_page.py` | pagina todos los clientes (`customers(first, after)`) con `defaultAddress { company }`, usada por la reconciliación de la app `customers`. Verificado contra la cuenta real (10.000 clientes) el 2026-09-15. | 2026-09-15 |

## Convenciones

- **Nombre**: la constante tal cual está en `queries.py`
  (`SCREAMING_SNAKE_CASE`, en inglés).
- **Tipo**: `query` o `mutation`, tal como lo declara el documento GraphQL.
- **Usada en**: el archivo de `functions/` que la invoca. Si más de una
  function la usa, se listan todas.
- Una consulta se **amplía** (agregar un campo) en vez de crear una
  parecida, salvo que traiga datos de un recurso distinto de Shopify.
