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

# ESCRITURAS EXTERNAS — doble compuerta, desactivadas por defecto.
EXTERNAL_WRITES_ENABLED = config("EXTERNAL_WRITES_ENABLED", default=False, cast=bool)
SIIGO_INVOICE_WRITES_ENABLED = config("SIIGO_INVOICE_WRITES_ENABLED", default=False, cast=bool)

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

# PEDIDOS LOCAL CONTROLADO
# Las lecturas y escrituras externas nacen apagadas. El modo demo local sólo
# puede abrir sesión cuando DEBUG=True; views.py vuelve a comprobar ambas
# condiciones para que esta puerta no pueda habilitarse en Producción.
ORDERS_LOCAL_MODE = config("ORDERS_LOCAL_MODE", default=DEBUG, cast=bool)
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
ORDERS_SYNC_FROM = config("ORDERS_SYNC_FROM", default="2026-06-27")
ORDERS_PDF_FROM = config("ORDERS_PDF_FROM", default="2026-08-08")
ORDERS_SYNC_INTERVAL_MINUTES = config(
    "ORDERS_SYNC_INTERVAL_MINUTES", default=15, cast=int
)
PAMO_CANONICAL_API_BASE_URL = config("PAMO_CANONICAL_API_BASE_URL", default="")
PAMO_API_TOKEN = config("PAMO_API_TOKEN", default="")
