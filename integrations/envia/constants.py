# Hosts fijos de Envía -- no son secretos, dependen solo del ambiente
# (config/constants.py ENVIA_ENVIRONMENT), no de la cuenta.

SHIPPING_BASE = {
    "sandbox": "https://api-test.envia.com",
    "production": "https://api.envia.com",
}
QUERIES_BASE = {
    "sandbox": "https://queries.test.envia.com",
    "production": "https://queries.envia.com",
}
