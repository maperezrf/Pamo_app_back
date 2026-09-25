from decouple import Csv, config

# DJANGO CORE
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", cast=bool)

# GOOGLE OAUTH
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID")

# GITHUB WEBHOOK (feature_tracking)
GITHUB_WEBHOOK_SECRET = config("GITHUB_WEBHOOK_SECRET")

# API KEY para consumidores máquina-a-máquina internos (ej. servidor MCP)
MCP_API_KEY = config("MCP_API_KEY")

# SHOPIFY (integrations/shopify/) -- por ahora solo se usa la API GraphQL.
SHOPIFY_GRAPHQL_URL = config("SHOPIFY_GRAPHQL_URL")
SHOPIFY_ACCESS_TOKEN = config("SHOPIFY_ACCESS_TOKEN")
# Secreto para verificar la firma de los webhooks entrantes (app customers)
# -- distinto de SHOPIFY_ACCESS_TOKEN, se consigue en la config de la app
# personalizada del Shopify Admin de esta tienda.
SHOPIFY_WEBHOOK_SECRET = config("SHOPIFY_WEBHOOK_SECRET")

# FALABELLA (integrations/falabella/) -- Seller Center API, autenticación
# por firma HMAC (sin token Bearer).
FALABELLA_USER_ID = config("FALABELLA_USER_ID")
FALABELLA_API_KEY = config("FALABELLA_API_KEY")
# Cliente fijo de Shopify al que se asignan todos los pedidos de Falabella
# (app orders). Id numérico, sin prefijo gid://. No es secreto, pero cambia
# entre tiendas/entornos.
FALABELLA_SHOPIFY_CUSTOMER_ID = config("FALABELLA_SHOPIFY_CUSTOMER_ID", default="")
# Bodega (Location de Shopify) que se prefiere para despachar pedidos de
# marketplace (app orders). Id numérico sin prefijo gid://. Vacía = sin
# prioridad.
FULFILLMENT_PRIORITY_LOCATION_ID = config("FULFILLMENT_PRIORITY_LOCATION_ID", default="")

# MERCADO LIBRE (integrations/mercadolibre/) -- OAuth2. El token (access +
# refresh, que rota en cada renovación) vive en BD
# (integrations.mercadolibre.models.MercadoLibreToken), no en entorno; se
# obtiene conectando la cuenta en GET /api/integrations/mercadolibre/connect/.
# MERCADOLIBRE_REDIRECT_URI debe ser la URL pública de
# /api/integrations/mercadolibre/callback/, igual a la registrada en la app.
# Vacías por defecto para que el backend arranque sin Mercado Libre.
MERCADOLIBRE_CLIENT_ID = config("MERCADOLIBRE_CLIENT_ID", default="")
MERCADOLIBRE_CLIENT_SECRET = config("MERCADOLIBRE_CLIENT_SECRET", default="")
MERCADOLIBRE_REDIRECT_URI = config("MERCADOLIBRE_REDIRECT_URI", default="")
MERCADOLIBRE_API_BASE_URL = config("MERCADOLIBRE_API_BASE_URL", default="https://api.mercadolibre.com")
MERCADOLIBRE_AUTH_URL = config("MERCADOLIBRE_AUTH_URL", default="https://auth.mercadolibre.com.co/authorization")
# Cliente fijo de Shopify al que se asignan todos los pedidos de Mercado
# Libre (app orders), igual que FALABELLA_SHOPIFY_CUSTOMER_ID: Mercado Libre
# no entrega email ni teléfono del comprador. Id numérico, sin gid://.
MERCADOLIBRE_SHOPIFY_CUSTOMER_ID = config("MERCADOLIBRE_SHOPIFY_CUSTOMER_ID", default="")

# MADECENTRO (integrations/madecentro/) -- API de Shipturtle, token Bearer
# fijo que no vence. Shipturtle entrega un token por dominio (pedidos,
# productos); por ahora solo se usa el de pedidos. Vacío por defecto para
# que el backend arranque sin Madecentro.
MADECENTRO_API_BASE_URL = config("MADECENTRO_API_BASE_URL", default="https://api.shipturtle.com/api/v1")
MADECENTRO_ORDERS_TOKEN = config("MADECENTRO_ORDERS_TOKEN", default="")

# SODIMAC (integrations/sodimac/) -- autenticación por subscription key
# (Azure APIM), no token Bearer.
SODIMAC_SUBSCRIPTION_KEY = config("SODIMAC_SUBSCRIPTION_KEY")
SODIMAC_REPORT_URL = config("SODIMAC_REPORT_URL")
SODIMAC_REINJECT_URL = config("SODIMAC_REINJECT_URL")
SODIMAC_PROVIDER_REFERENCE = config("SODIMAC_PROVIDER_REFERENCE")

# SIIGO (integrations/siigo/) -- autenticación por token (username +
# access_key -> token cacheado en integrations.siigo.models.SiigoToken).
SIIGO_USERNAME = config("SIIGO_USERNAME")
SIIGO_ACCESS_KEY = config("SIIGO_ACCESS_KEY")
SIIGO_PARTNER_ID = config("SIIGO_PARTNER_ID")
SIIGO_AUTH_URL = config("SIIGO_AUTH_URL")
SIIGO_API_BASE_URL = config("SIIGO_API_BASE_URL")

# ENVÍA (integrations/envia/) -- autenticación por token Bearer.
# ENVIA_ENVIRONMENT: "sandbox" o "production" -- decide qué host se usa.
ENVIA_API_TOKEN = config("ENVIA_API_TOKEN")
ENVIA_ENVIRONMENT = config("ENVIA_ENVIRONMENT", default="sandbox")
ENVIA_ALLOWED_CARRIERS = config("ENVIA_ALLOWED_CARRIERS", default="", cast=Csv())

# WHATSAPP (integrations/whatsapp/) -- WhatsApp Cloud API (Meta), autenticación
# por token Bearer. Credenciales pendientes de conseguir.
WHATSAPP_ACCESS_TOKEN = config("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = config("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_BUSINESS_ACCOUNT_ID = config("WHATSAPP_BUSINESS_ACCOUNT_ID")
WHATSAPP_APP_SECRET = config("WHATSAPP_APP_SECRET")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = config("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
WHATSAPP_API_VERSION = config("WHATSAPP_API_VERSION", default="v21.0")

# DATABASE
# Vacío en desarrollo local (se usa SQLite). En Railway apunta al Postgres
# del proyecto -- ver config/settings.py.
DATABASE_URL = config("DATABASE_URL", default="")

# HOSTS / CORS / CSRF
# Comma-separated en la variable de entorno (ej. "https://app.dominio.com,https://otro.dominio.com").
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:5173,http://127.0.0.1:5173",
    cast=Csv(),
)
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://localhost:5173,http://127.0.0.1:5173",
    cast=Csv(),
)
