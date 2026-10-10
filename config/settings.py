"""Django settings for the Moshtari-Yab MVP."""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

env_file = BASE_DIR / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=False.")
    SECRET_KEY = "development-only-customer-yab-key"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "https://ebi852.ir,https://www.ebi852.ir,http://ebi852.ir,http://www.ebi852.ir",
    ).split(",")
    if origin.strip()
]

# Behind nginx / a TLS proxy (X-Forwarded-Proto and X-Forwarded-Host are trusted).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
if os.environ.get("DJANGO_BEHIND_HTTPS_PROXY", "").lower() in {"1", "true", "yes"}:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.businesses.apps.BusinessesConfig",
    "apps.products.apps.ProductsConfig",
    "apps.discovery.apps.DiscoveryConfig",
    "apps.billing.apps.BillingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.flags",
                "apps.billing.context_processors.wallet",
                "apps.discovery.context_processors.x_panel",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# One PostgreSQL database for everything: Django (public schema), crawler (crawler schema) and need_engine
# (need_engine schema, with pgvector). The same POSTGRES_* variables are read by the crawler and the engine.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "customer_yab"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
    }
}

LANGUAGE_CODE = "fa-ir"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Serve uploaded product images from Django itself (fine for the MVP; use a CDN/nginx later).
SERVE_MEDIA = os.environ.get("DJANGO_SERVE_MEDIA", "true").lower() in {"1", "true", "yes"}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

X_INTENT_BASE_URL = os.getenv("X_INTENT_BASE_URL", "https://x.com/intent/post")
X_PANEL_ENABLED = os.getenv("X_PANEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email Configuration (SMTP)
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in {"1", "true", "yes"}
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "sitebartar3@gmail.com")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "sitebartar3@gmail.com")

FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# Auth Redirects
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

# need_engine (analysis core) — same env names the engine itself uses
# need_engine's schema in the same database (opportunities to import, cost ledger for the dashboard).
NEED_ENGINE_SCHEMA = os.environ.get("NE_STATE_SCHEMA", "need_engine")
# public address of this panel: reply drafts link to <base>/r/<product>/?ref=<opportunity> (click counter → seller's site)
PUBLIC_BASE_URL = os.environ.get("NE_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
# NE_PANEL_MOCK_LLM=true: «نمونه بساز»، «دوباره بنویس» و «پر کردن از لینک» بدون API (تست/دمو)
PANEL_MOCK_LLM = os.environ.get("NE_PANEL_MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")
