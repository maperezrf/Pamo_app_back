from decouple import config

# DJANGO CORE
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", cast=bool)

# GOOGLE OAUTH
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID")
