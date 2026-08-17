from decouple import Csv, config

# DJANGO CORE
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", cast=bool)

# GOOGLE OAUTH
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID")

# DATABASE
# Vacío en desarrollo local (se usa SQLite). En Railway apunta al Postgres
# del proyecto -- ver config/settings.py.
DATABASE_URL = config("DATABASE_URL", default="")

# HOSTS / CORS / CSRF
# Comma-separated en la variable de entorno (ej. "https://app.dominio.com,https://otro.dominio.com").
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:5173", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="http://localhost:5173", cast=Csv())
