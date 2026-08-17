from decouple import config

# DJANGO CORE
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", cast=bool)

# GOOGLE OAUTH
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID")

# GITHUB WEBHOOK (feature_tracking)
GITHUB_WEBHOOK_SECRET = config("GITHUB_WEBHOOK_SECRET")

# API KEY para consumidores máquina-a-máquina internos (ej. servidor MCP)
MCP_API_KEY = config("MCP_API_KEY")
