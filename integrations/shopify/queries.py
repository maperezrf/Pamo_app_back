# Catálogo de documentos GraphQL de Shopify. Antes de agregar uno nuevo,
# revisar integrations/shopify/QUERIES.md -- puede que ya exista una
# consulta reutilizable para lo que hace falta.

# `inventoryItem` (bodegas y unidades disponibles) verificado contra la
# cuenta real el 2026-09-23 (API 2024-07): `inventoryLevels` solo lista las
# bodegas donde el ítem está dado de alta; `quantities(names:)` reemplaza
# al campo `available` obsoleto. Costo real ~8 puntos.
GET_VARIANT_BY_SKU = """
query GetVariantBySku($query: String!) {
  productVariants(first: 1, query: $query) {
    edges {
      node {
        id
        sku
        product {
          id
          title
        }
        inventoryItem {
          tracked
          inventoryLevels(first: 20) {
            edges {
              node {
                location {
                  id
                  name
                }
                quantities(names: ["available"]) {
                  name
                  quantity
                }
              }
            }
          }
        }
      }
    }
  }
}
""".strip()

# Campos de entrada verificados por introspección contra el schema real de
# la tienda (API 2024-07) en integrations/shopify/QUERIES.md -- no
# adivinados, porque esto crea pedidos reales.
CREATE_ORDER = """
mutation CreateOrder($order: OrderCreateOrderInput!) {
  orderCreate(order: $order) {
    order {
      id
      name
      displayFinancialStatus
    }
    userErrors {
      field
      message
    }
  }
}
""".strip()

# CustomerInput verificado por introspección contra el schema real de la
# tienda el 2026-09-15: NO tiene ningún campo de dirección (ni `addresses`
# ni `defaultAddress`) -- la cédula (en `company`) se escribe aparte con
# CREATE_CUSTOMER_ADDRESS/UPDATE_CUSTOMER_ADDRESS.
CREATE_CUSTOMER = """
mutation CreateCustomer($input: CustomerInput!) {
  customerCreate(input: $input) {
    customer {
      id
      email
      phone
      firstName
      lastName
    }
    userErrors {
      field
      message
    }
  }
}
""".strip()

# MailingAddressInput verificado por introspección el 2026-09-15: address1,
# address2, city, company, countryCode, firstName, lastName, phone,
# provinceCode, zip. `customerAddressCreate` es para un cliente que
# TODAVÍA NO tiene ninguna dirección.
CREATE_CUSTOMER_ADDRESS = """
mutation CreateCustomerAddress($customerId: ID!, $address: MailingAddressInput!, $setAsDefault: Boolean) {
  customerAddressCreate(customerId: $customerId, address: $address, setAsDefault: $setAsDefault) {
    address {
      id
      company
    }
    userErrors {
      field
      message
    }
  }
}
""".strip()

# Para un cliente que YA tiene una dirección (`addressId` conocido -- ver
# ShopifyCustomer.default_address_id en la app `customers`, así no hace
# falta leer Shopify antes de poder escribir).
UPDATE_CUSTOMER_ADDRESS = """
mutation UpdateCustomerAddress($customerId: ID!, $addressId: ID!, $address: MailingAddressInput!, $setAsDefault: Boolean) {
  customerAddressUpdate(customerId: $customerId, addressId: $addressId, address: $address, setAsDefault: $setAsDefault) {
    address {
      id
      company
    }
    userErrors {
      field
      message
    }
  }
}
""".strip()

# Paginación usada por la reconciliación (customers.reconcile_shopify).
# `customers(first, after)` y `defaultAddress { company }` verificados
# contra la cuenta real el 2026-09-15 (10.000 clientes reales escaneados).
# `updatedAt` no se reverificó en esa misma sesión -- es un campo estándar
# del schema de Shopify, pero si `LIST_CUSTOMERS_PAGE` fallara al usarse por
# primera vez, revisar este campo primero.
LIST_CUSTOMERS_PAGE = """
query ListCustomersPage($cursor: String) {
  customers(first: 100, after: $cursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        email
        phone
        firstName
        lastName
        updatedAt
        defaultAddress {
          id
          company
        }
      }
    }
  }
}
""".strip()


GET_VARIANTS_BY_SKUS = """
query GetVariantsBySkus($query: String!, $cursor: String) {
  productVariants(first: 250, after: $cursor, query: $query) {
    edges {
      node {
        id
        sku
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()


# Pedidos (app `orders`: copia local sincronizada por webhook y
# reconciliación). Campos verificados por introspección contra la tienda real
# el 2026-10-05. Email y teléfono se leen del pedido (`Order.email`/`phone`):
# en `Customer` están deprecados. Costo medido de `LIST_ORDERS_PAGE`: 72
# puntos con `first: 50` (límite 1000). `ORDER_FIELDS` lo comparten
# `LIST_ORDERS_PAGE` y `GET_ORDER` para que las dos se normalicen igual.
ORDER_FIELDS = """
fragment OrderFields on Order {
  id
  name
  createdAt
  updatedAt
  cancelledAt
  displayFinancialStatus
  displayFulfillmentStatus
  tags
  email
  phone
  totalPriceSet {
    shopMoney {
      amount
      currencyCode
    }
  }
  customer {
    id
    firstName
    lastName
    defaultAddress {
      company
      city
      province
      address1
    }
  }
}

fragment LineItemFields on LineItem {
  sku
  name
  quantity
  originalUnitPriceSet {
    shopMoney {
      amount
    }
  }
}
""".strip()

LIST_ORDERS_PAGE = (
    """
query ListOrdersPage($first: Int!, $after: String, $query: String, $sortKey: OrderSortKeys!, $reverse: Boolean!) {
  orders(first: $first, after: $after, query: $query, sortKey: $sortKey, reverse: $reverse) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ...OrderFields
      lineItems(first: 30) {
        pageInfo {
          hasNextPage
        }
        nodes {
          ...LineItemFields
        }
      }
    }
  }
}
""".strip()
    + "\n\n"
    + ORDER_FIELDS
)

# Un pedido por id, con todas sus líneas (paginadas de a 100 con
# `$linesAfter`). `order` devuelve `null` si el pedido no existe o se borró.
GET_ORDER = (
    """
query GetOrder($id: ID!, $linesAfter: String) {
  order(id: $id) {
    ...OrderFields
    lineItems(first: 100, after: $linesAfter) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        ...LineItemFields
      }
    }
  }
}
""".strip()
    + "\n\n"
    + ORDER_FIELDS
)
