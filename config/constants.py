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
# Integraciones y escrituras fallan cerradas. Las lecturas puntuales se
# habilitan solo durante un proceso explícito de sincronización.
EXTERNAL_WRITES_ENABLED = config("EXTERNAL_WRITES_ENABLED", default=False, cast=bool)
SIIGO_INVOICE_WRITES_ENABLED = config("SIIGO_INVOICE_WRITES_ENABLED", default=False, cast=bool)

# Siigo de solo lectura; vacías en desarrollo cuando no se sincroniza.
SIIGO_USERNAME = config("SIIGO_USERNAME", default="")
SIIGO_ACCESS_KEY = config("SIIGO_ACCESS_KEY", default="")
SIIGO_PARTNER_ID = config("SIIGO_PARTNER_ID", default="")
SIIGO_LIVE_READS_ENABLED = config("SIIGO_LIVE_READS_ENABLED", default=False, cast=bool)
# Si Siigo ofrece varias opciones activas, estos identificadores evitan que el
# sistema escoja al azar. Permanecen vacíos hasta validar la cuenta objetivo.
SIIGO_INVOICE_DOCUMENT_ID = config("SIIGO_INVOICE_DOCUMENT_ID", default="")
SIIGO_PAYMENT_TYPE_ID = config("SIIGO_PAYMENT_TYPE_ID", default="")
SIIGO_DEFAULT_SELLER_ID = config("SIIGO_DEFAULT_SELLER_ID", default="")

# Lectura estructurada de facturas de proveedor. En local funciona primero el
# lector determinista de PDF; sin clave, imágenes/Excel fallan de forma segura
# y permiten continuar manualmente.
OPENAI_API_KEY = config("OPENAI_API_KEY", default="")
REMITTANCE_AI_MODEL = config("REMITTANCE_AI_MODEL", default="gpt-5.6-luna")
REMITTANCE_SUPPLIER_INVOICE_DAILY_LIMIT = config(
    "REMITTANCE_SUPPLIER_INVOICE_DAILY_LIMIT",
    default=5,
    cast=int,
)

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
