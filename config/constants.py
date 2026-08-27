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

# ESCRITURAS EXTERNAS — todas las compuertas nacen apagadas. Una salida real
# de WhatsApp exige las tres compuertas; el proveedor mock no hace red.
EXTERNAL_WRITES_ENABLED = config("EXTERNAL_WRITES_ENABLED", default=False, cast=bool)
SIIGO_INVOICE_WRITES_ENABLED = config("SIIGO_INVOICE_WRITES_ENABLED", default=False, cast=bool)
SHOPIFY_READS_ENABLED = config("SHOPIFY_READS_ENABLED", default=False, cast=bool)
MESSAGING_EXTERNAL_WRITES_ENABLED = config(
    "MESSAGING_EXTERNAL_WRITES_ENABLED", default=False, cast=bool
)
PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED = config(
    "PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED", default=False, cast=bool
)
PAMO_WHATSAPP_PROVIDER = config("PAMO_WHATSAPP_PROVIDER", default="mock")
PAMO_WHATSAPP_PILOT_RECIPIENT = config(
    "PAMO_WHATSAPP_PILOT_RECIPIENT", default=""
)
PAMO_WHATSAPP_PILOT_RECIPIENT_NAME = config(
    "PAMO_WHATSAPP_PILOT_RECIPIENT_NAME", default="Piloto autorizado"
)
PAMO_WHATSAPP_INTERNAL_COPY_FROM = config(
    "PAMO_WHATSAPP_INTERNAL_COPY_FROM", default=""
)
PAMO_WHATSAPP_INTERNAL_TEMPLATE_VERSION = config(
    "PAMO_WHATSAPP_INTERNAL_TEMPLATE_VERSION", default="internal-order-v1"
)
PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED = config(
    "PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED", default=False, cast=bool
)
PAMO_WHATSAPP_DEPLOYMENT_TIER = config(
    "PAMO_WHATSAPP_DEPLOYMENT_TIER", default="local"
)
PAMO_WHATSAPP_AUTO_PREPARE_ENABLED = config(
    "PAMO_WHATSAPP_AUTO_PREPARE_ENABLED", default=False, cast=bool
)
PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED = config(
    "PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED", default=False, cast=bool
)
PAMO_WHATSAPP_GUIDE_AUTO_SEND_ENABLED = config(
    "PAMO_WHATSAPP_GUIDE_AUTO_SEND_ENABLED", default=False, cast=bool
)
SHOPIFY_SYNC_SCAN_ENABLED = config("SHOPIFY_SYNC_SCAN_ENABLED", default=False, cast=bool)
SHOPIFY_SYNC_WRITES_ENABLED = config("SHOPIFY_SYNC_WRITES_ENABLED", default=False, cast=bool)
SHOPIFY_SYNC_MAX_BATCH = config("SHOPIFY_SYNC_MAX_BATCH", default=5, cast=int)
SHOPIFY_SYNC_SOURCE_MAX_AGE_MINUTES = config(
    "SHOPIFY_SYNC_SOURCE_MAX_AGE_MINUTES", default=360, cast=int
)

# Credenciales usadas solo por los comandos explícitos de lectura Siigo.
SIIGO_USERNAME = config("SIIGO_USERNAME", default="")
SIIGO_ACCESS_KEY = config("SIIGO_ACCESS_KEY", default="")
SIIGO_PARTNER_ID = config("SIIGO_PARTNER_ID", default="")

# Meta Cloud API. Permanecen vacías en el laboratorio y nunca se exponen al
# frontend. Railway ya usa el prefijo META_WHATSAPP_; los alias mantienen
# compatibilidad con instalaciones anteriores sin copiar ni revelar secretos.
def _config_first(*names, default=""):
    for name in names:
        value = config(name, default="")
        if value:
            return value
    return default


META_APP_ID = _config_first("META_APP_ID", "META_WHATSAPP_APP_ID")
META_APP_SECRET = _config_first("META_APP_SECRET", "META_WHATSAPP_APP_SECRET")
META_WABA_ID = _config_first("META_WABA_ID", "META_WHATSAPP_WABA_ID")
META_PHONE_NUMBER_ID = _config_first(
    "META_PHONE_NUMBER_ID", "META_WHATSAPP_PHONE_NUMBER_ID"
)
META_SYSTEM_USER_TOKEN = _config_first(
    "META_SYSTEM_USER_TOKEN", "META_WHATSAPP_ACCESS_TOKEN"
)
META_VERIFY_TOKEN = _config_first(
    "META_VERIFY_TOKEN", "META_WHATSAPP_WEBHOOK_VERIFY_TOKEN"
)
META_WEBHOOK_URL = _config_first("META_WEBHOOK_URL", "META_WHATSAPP_WEBHOOK_URL")
META_GRAPH_API_VERSION = _config_first(
    "META_GRAPH_API_VERSION", "META_WHATSAPP_GRAPH_API_VERSION"
)
META_TEMPLATE_INITIAL_NAME = config("META_TEMPLATE_INITIAL_NAME", default="")
META_TEMPLATE_INITIAL_LANGUAGE = config("META_TEMPLATE_INITIAL_LANGUAGE", default="")
META_TEMPLATE_FOLLOWUP_NAME = config("META_TEMPLATE_FOLLOWUP_NAME", default="")
META_TEMPLATE_FOLLOWUP_LANGUAGE = config("META_TEMPLATE_FOLLOWUP_LANGUAGE", default="")
_META_RECIPIENT_ALLOWLIST_RAW = _config_first(
    "META_RECIPIENT_ALLOWLIST",
    "META_WHATSAPP_RECIPIENT_ALLOWLIST",
    "META_WHATSAPP_ALLOWED_RECIPIENTS",
)
META_RECIPIENT_ALLOWLIST = [
    item.strip() for item in _META_RECIPIENT_ALLOWLIST_RAW.split(",") if item.strip()
]

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

# AISLAMIENTO ENTRE PROTOTIPOS LOCALES
# Los navegadores comparten cookies por host, no por puerto. Estos nombres son
# configurables para que una copia local integrada no invalide la sesion de
# otro prototipo que tambien use 127.0.0.1 o localhost.
SESSION_COOKIE_NAME = config("SESSION_COOKIE_NAME", default="sessionid")
CSRF_COOKIE_NAME = config("CSRF_COOKIE_NAME", default="csrftoken")

# PEDIDOS LOCAL CONTROLADO
# Las lecturas y escrituras externas nacen apagadas. El modo demo local sólo
# puede abrir sesión cuando DEBUG=True; views.py vuelve a comprobar ambas
# condiciones para que esta puerta no pueda habilitarse en Producción.
ORDERS_LOCAL_MODE = config("ORDERS_LOCAL_MODE", default=True, cast=bool)
ORDERS_EXTERNAL_READS_ENABLED = config(
    "ORDERS_EXTERNAL_READS_ENABLED", default=False, cast=bool
)
ORDERS_EXTERNAL_WRITES_ENABLED = config(
    "ORDERS_EXTERNAL_WRITES_ENABLED", default=False, cast=bool
)
ORDERS_GUIDE_MAX_BYTES = config(
    "ORDERS_GUIDE_MAX_BYTES", default=10 * 1024 * 1024, cast=int
)
LOCAL_DEMO_AUTH_ENABLED = config("LOCAL_DEMO_AUTH_ENABLED", default=False, cast=bool)
