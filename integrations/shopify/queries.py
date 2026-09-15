# Catálogo de documentos GraphQL de Shopify. Antes de agregar uno nuevo,
# revisar integrations/shopify/QUERIES.md -- puede que ya exista una
# consulta reutilizable para lo que hace falta.

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
