"""
Django settings for config project.
"""

from pathlib import Path

import dj_database_url

from config.constants import *

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ALLOWED_HOSTS / CORS_ALLOWED_ORIGINS / CSRF_TRUSTED_ORIGINS ya llegan
# resueltos desde config.constants (import * de arriba) -- fuente única,
# nunca hardcodeados acá.


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'accounts',
    'feature_tracking',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# Sin DATABASE_URL (desarrollo local): SQLite, sin fricción para levantar el
# proyecto. Con DATABASE_URL seteada (Railway): Postgres del proyecto.
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600),
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization

LANGUAGE_CODE = 'es-co'

TIME_ZONE = 'America/Bogota'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Django REST Framework
# Defaults explícitos a propósito: cada APIView nueva parte de "requiere
# sesión iniciada" salvo que declare lo contrario. Ver auditoría de pamo_web,
# que no tenía este bloque y dependía de que cada vista lo declarara a mano.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}


# CORS / cookies de sesión entre el frontend y esta API.
# ALLOWED_HOSTS / CORS_ALLOWED_ORIGINS / CSRF_TRUSTED_ORIGINS vienen de
# config.constants (env var, comma-separated) -- ver arriba.
CORS_ALLOW_CREDENTIALS = True

# En producción, backend y frontend quedan en subdominios distintos
# (*.up.railway.app) -- dominios distintos exigen SameSite=None + Secure
# para que la cookie de sesión viaje entre ellos (ver GOVERNANCE.md backend
# §12 / frontend §7). En desarrollo local (DEBUG=True, http) se deja el
# default de Django para no romper la sesión sin HTTPS.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"
