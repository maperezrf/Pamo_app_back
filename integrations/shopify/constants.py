# Vocabulario propio de la API de Shopify -- no son secretos, son valores
# fijos que la API exige (ej. enums de GraphQL). Verificado por introspección
# contra el schema real (API 2024-07) antes de usarse.

FINANCIAL_STATUSES = frozenset({
    "PENDING",
    "AUTHORIZED",
    "PARTIALLY_PAID",
    "PAID",
    "PARTIALLY_REFUNDED",
    "REFUNDED",
    "VOIDED",
    "EXPIRED",
})
