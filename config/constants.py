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
PAMO_WHATSAPP_AUTO_PREPARE_ENABLED = config(
    "PAMO_WHATSAPP_AUTO_PREPARE_ENABLED", default=True, cast=bool
)
PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED = config(
    "PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED", default=False, cast=bool
)

# Credenciales usadas solo por los comandos explícitos de lectura Siigo.
SIIGO_USERNAME = config("SIIGO_USERNAME", default="")
SIIGO_ACCESS_KEY = config("SIIGO_ACCESS_KEY", default="")
SIIGO_PARTNER_ID = config("SIIGO_PARTNER_ID", default="")

# Meta Cloud API. Permanecen vacías en el laboratorio y nunca se exponen al
# frontend. Los identificadores observados no se codifican como constantes.
META_APP_ID = config("META_APP_ID", default="")
META_APP_SECRET = config("META_APP_SECRET", default="")
META_WABA_ID = config("META_WABA_ID", default="")
META_PHONE_NUMBER_ID = config("META_PHONE_NUMBER_ID", default="")
META_SYSTEM_USER_TOKEN = config("META_SYSTEM_USER_TOKEN", default="")
META_VERIFY_TOKEN = config("META_VERIFY_TOKEN", default="")
META_WEBHOOK_URL = config("META_WEBHOOK_URL", default="")
META_GRAPH_API_VERSION = config("META_GRAPH_API_VERSION", default="")
META_TEMPLATE_INITIAL_NAME = config("META_TEMPLATE_INITIAL_NAME", default="")
META_TEMPLATE_INITIAL_LANGUAGE = config("META_TEMPLATE_INITIAL_LANGUAGE", default="")
META_TEMPLATE_FOLLOWUP_NAME = config("META_TEMPLATE_FOLLOWUP_NAME", default="")
META_TEMPLATE_FOLLOWUP_LANGUAGE = config("META_TEMPLATE_FOLLOWUP_LANGUAGE", default="")
META_RECIPIENT_ALLOWLIST = config("META_RECIPIENT_ALLOWLIST", default="", cast=Csv())

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
