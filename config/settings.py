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

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=False.")
    SECRET_KEY = "development-only-customer-yab-key"
elif not DEBUG and SECRET_KEY.startswith(("replace-with-", "development-only")):
    raise ImproperlyConfigured("Replace the example DJANGO_SECRET_KEY with a unique secret before production.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]

# Trust forwarded headers only when explicitly deployed behind a trusted TLS proxy.
BEHIND_HTTPS_PROXY = os.environ.get("DJANGO_BEHIND_HTTPS_PROXY", "").lower() in {"1", "true", "yes", "on"}
if BEHIND_HTTPS_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

SESSION_COOKIE_SECURE = BEHIND_HTTPS_PROXY or os.environ.get("DJANGO_FORCE_SECURE_COOKIES", "").lower() in {"1", "true", "yes", "on"}
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() in {"1", "true", "yes", "on"}
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0") or 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "false").lower() in {"1", "true", "yes", "on"}
SECURE_HSTS_PRELOAD = os.environ.get("DJANGO_SECURE_HSTS_PRELOAD", "false").lower() in {"1", "true", "yes", "on"}
SECURE_REFERRER_POLICY = os.environ.get("DJANGO_SECURE_REFERRER_POLICY", "same-origin")
SECURE_CONTENT_TYPE_NOSNIFF = True

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
]
try:
    import whitenoise  # noqa: F401
    MIDDLEWARE.append("whitenoise.middleware.WhiteNoiseMiddleware")
except ImportError:
    pass

MIDDLEWARE.extend([
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.OnboardingRequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
])

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
                "apps.core.context_processors.appearance",
                "apps.billing.context_processors.wallet",
                "apps.discovery.context_processors.x_panel",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
TEST_RUNNER = "config.test_runner.GcDiscoverRunner"  # closes stray DB sessions before the test DB is dropped
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
# seller pages «جستجوی مشتری در X» (per-product X search, NE_X_MODE=product|both)
X_SELLER_UI = os.getenv("X_SELLER_UI", "true").lower() in {"1", "true", "yes", "on"}
X_PANEL_ENABLED = os.getenv("X_PANEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email Configuration (SMTP)
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in {"1", "true", "yes"}
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)

FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# Auth Redirects
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

# need_engine (analysis core) — same env names the engine itself uses
# need_engine's schema in the same database (opportunities to import, cost ledger for the dashboard).
NEED_ENGINE_SCHEMA = os.environ.get("NE_STATE_SCHEMA", "need_engine")
TG_DB_SCHEMA = os.environ.get("TG_DB_SCHEMA", "crawler")
# public address of this panel: reply drafts link to <base>/r/<product>/?ref=<opportunity> (click counter → seller's site)
PUBLIC_BASE_URL = os.environ.get("NE_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
# NE_PANEL_MOCK_LLM=true: «نمونه بساز»، «دوباره بنویس» و «پر کردن از لینک» بدون API (تست/دمو)
PANEL_MOCK_LLM = os.environ.get("NE_PANEL_MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")

# Show pre-seeded test seller accounts on login page
SHOW_DEMO_ACCOUNTS = os.environ.get("SHOW_DEMO_ACCOUNTS", "false").strip().lower() in ("1", "true", "yes", "on")
DATA_RETENTION_DAYS = max(1, int(os.environ.get("DATA_RETENTION_DAYS", "180") or 180))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

# ==============================================================================
# CELERY & REDIS DISTRIBUTED TASK QUEUE CONFIGURATION
# ==============================================================================
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)

# Exactly ONE Instagram DM dispatcher may run, otherwise two loops message the same leads with the same senders:
#   "command" -> docker service `ig-outreach` (manage.py run_instagram_outreach --loop)   [default]
#   "celery"  -> celery-beat schedules apps.discovery.tasks.task_dispatch_instagram_outreach
# Minimum comments a post needs before its commenters are inspected (scraper
# family path, legacy path and the manage dashboard funnel all read this).
INSTAGRAM_MIN_COMMENTS = max(1, int(os.environ.get("INSTAGRAM_MIN_COMMENTS", os.environ.get("INSTAGRAM_PAGE_MIN_COMMENTS", "10"))))
INSTAGRAM_OUTREACH_DISPATCHER = os.environ.get("INSTAGRAM_OUTREACH_DISPATCHER", "command").strip().lower()
if INSTAGRAM_OUTREACH_DISPATCHER not in {"command", "celery"}:
    raise ImproperlyConfigured("INSTAGRAM_OUTREACH_DISPATCHER must be 'command' or 'celery'.")
# Global pacing for cold DMs (both dispatchers): at most one cold DM per this many minutes across all
# senders (+0-50% jitter). Per-account tier delays and daily/hourly caps apply on top of it.
INSTAGRAM_OUTREACH_MIN_GAP_MINUTES = max(0, int(os.environ.get("INSTAGRAM_OUTREACH_MIN_GAP_MINUTES", "20")))
# Inbox reply checks: only during the senders' shift hours, every random 5-10 minutes (not a fixed 2-minute poll
# around the clock, which no person does).
INSTAGRAM_REPLY_SYNC_MIN_SECONDS = max(60, int(os.environ.get("INSTAGRAM_REPLY_SYNC_MIN_SECONDS", "300")))
INSTAGRAM_REPLY_SYNC_MAX_SECONDS = max(INSTAGRAM_REPLY_SYNC_MIN_SECONDS,
                                       int(os.environ.get("INSTAGRAM_REPLY_SYNC_MAX_SECONDS", "600")))
# Rotate a sender's 4G IP before every DM (old behaviour). Off: the IP is rotated only after a proxy error.
INSTAGRAM_PROXY_ROTATE_EACH_DM = os.environ.get("INSTAGRAM_PROXY_ROTATE_EACH_DM", "false").strip().lower() in {"1", "true", "yes", "on"}
# Instagram accounts (apps/discovery/ig_accounts.py). An account without its own proxy or system config talks
# through this proxy (the shared xray pool), never from the server's IP; "direct" = server IP (not recommended).
INSTAGRAM_DEFAULT_PROXY_URL = os.environ.get("INSTAGRAM_DEFAULT_PROXY_URL", "socks5h://xray:1080").strip()
# How many Instagram accounts may share one system V2Ray config (1 = every account has its own IP).
INSTAGRAM_ACCOUNTS_PER_CONFIG = max(1, int(os.environ.get("INSTAGRAM_ACCOUNTS_PER_CONFIG", "1")))
# A login started in the panel runs in a background thread of the web process and waits there for the code.
# Working-hours window and human-like pauses for Instagram. Only the test runner/conftest turn this off.
INSTAGRAM_ANTIBAN_ENABLED = os.environ.get("INSTAGRAM_ANTIBAN_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
# Mirror Instagram/X account changes into the accounts pool file (data/accounts_pool.json).
ACCOUNTS_POOL_FILE_SYNC = os.environ.get("ACCOUNTS_POOL_FILE_SYNC", "true").strip().lower() not in {"0", "false", "no", "off"}
INSTAGRAM_LOGIN_IN_BACKGROUND = os.environ.get("INSTAGRAM_LOGIN_IN_BACKGROUND", "true").strip().lower() in {"1", "true", "yes", "on"}
CELERY_TIMEZONE = "Asia/Tehran"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 mins max execution time per task
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# Tenants served by the Instagram DM dispatcher and reply sync, e.g. OUTREACH_TENANTS=tenant_4,tenant_7.
# Empty = every tenant that has an ACTIVE SENDER account (apps.discovery.ig_accounts.outreach_tenants).
OUTREACH_TENANTS = [t.strip() for t in os.getenv("OUTREACH_TENANTS", "").split(",") if t.strip()]

CELERY_TASK_ROUTES = {
    "apps.discovery.tasks.task_dispatch_instagram_outreach": {"queue": "outreach"},
    "apps.discovery.tasks.task_sync_lead_replies": {"queue": "outreach"},
    "apps.discovery.tasks.task_sync_telegram_channel_configs": {"queue": "periodic"},
    "apps.discovery.tasks.task_healthcheck_and_heal_proxies": {"queue": "periodic"},
    "apps.discovery.tasks.task_prune_dead_proxy_configs": {"queue": "periodic"},
}

CELERY_BEAT_SCHEDULE = {
    # 1. Instagram Outreach Dispatcher (runs every 12 minutes during active shifts)
    # Short tick; pacing lives in the DB (apps.discovery.outreach_engine), so most ticks send nothing.
    "dispatch-instagram-outreach-periodic": {
        "task": "apps.discovery.tasks.task_dispatch_instagram_outreach",
        "schedule": 60.0,
        "kwargs": {"batch_size": 2},   # tenants: OUTREACH_TENANTS (default: every tenant with an active sender)
        "options": {"expires": 55},
    },
    # 2. Lead Reply Sync: ticks every 5 minutes; the task itself skips outside shift hours and skips some ticks at
    #    random, so inbox checks land 5-15 minutes apart instead of on a fixed robotic beat.
    "sync-lead-replies-fast": {
        "task": "apps.discovery.tasks.task_sync_lead_replies",
        "schedule": 300.0,
        "options": {"expires": 280},
    },
    # 3. Telegram Proxy Channel Configs Sync (runs every 180 seconds)
    "sync-telegram-channel-configs": {
        "task": "apps.discovery.tasks.task_sync_telegram_channel_configs",
        "schedule": 180.0,  # 3 minutes
        "kwargs": {"max_posts": 15, "max_add": 10},
    },
    # 4. Proxy Healthcheck & Auto-Healing (runs every 5 minutes)
    "healthcheck-and-heal-proxies": {
        "task": "apps.discovery.tasks.task_healthcheck_and_heal_proxies",
        "schedule": 300.0,  # 5 minutes
    },
    # 5. Dead Proxy Configs Pruner (runs every 10 minutes)
    "prune-dead-proxy-configs": {
        "task": "apps.discovery.tasks.task_prune_dead_proxy_configs",
        "schedule": 600.0,  # 10 minutes
    },
}

# DM sending and reply polling belong to the selected dispatcher only (see INSTAGRAM_OUTREACH_DISPATCHER);
# with "command" the ig-outreach service does both, so Celery must not poll inboxes a second time.
if INSTAGRAM_OUTREACH_DISPATCHER != "celery":
    CELERY_BEAT_SCHEDULE.pop("dispatch-instagram-outreach-periodic", None)
    CELERY_BEAT_SCHEDULE.pop("sync-lead-replies-fast", None)
