import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-do-not-use-in-production")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")))
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "portal",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "portal.middleware.RequestLimitsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "portal.middleware.PrivateResponseMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]
PRIVATE_STORAGE_ROOT = Path(os.getenv("PRIVATE_STORAGE_ROOT", str(BASE_DIR / "var/private")))
PRIVATE_STORAGE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
FILE_UPLOAD_TEMP_DIR = str(PRIVATE_STORAGE_ROOT / "incoming")
Path(FILE_UPLOAD_TEMP_DIR).mkdir(exist_ok=True, mode=0o700)
DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'var/development.sqlite3'}"),
        conn_max_age=60,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "de"
TIME_ZONE = "Europe/Berlin"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 7 * 24 * 3600
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Enable only behind the deployment's trusted proxy, which must replace this header.
if os.getenv("TRUST_PROXY_HEADERS", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 128 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20
DATA_UPLOAD_MAX_NUMBER_FILES = 1
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
FILE_UPLOAD_HANDLERS = ["portal.middleware.BoundedUploadHandler"]
MAX_RECEIPT_BYTES = 10_000_000
MAX_SUBMISSION_BYTES = 50_000_000
MAX_SIGNATURE_BYTES = 1_000_000
MAX_FINAL_PDF_BYTES = int(os.getenv("MAX_FINAL_PDF_BYTES", "10000000"))
MAX_EXPENSES = 30
MAX_FILES_PER_EXPENSE = 3
MAX_PDF_PAGES = 30
MAX_CLAIM_PAGES = 100
MAX_IMAGE_PIXELS = 40_000_000
MAX_ACTIVE_DRAFTS = int(os.getenv("MAX_ACTIVE_DRAFTS", "1000"))
MAX_DRAFTS_PER_NETWORK = int(os.getenv("MAX_DRAFTS_PER_NETWORK", "300"))
TRUSTED_PROXY_CIDRS = list(filter(None, os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")))
MIN_FREE_STORAGE_BYTES = int(os.getenv("MIN_FREE_STORAGE_BYTES", "1000000000"))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "240"))
JOB_LEASE_SECONDS = JOB_TIMEOUT_SECONDS + 120
CONTENT_PURGE_ENABLED = os.getenv("CONTENT_PURGE_ENABLED", "0") == "1"
SUCCESS_RETENTION_DAYS = int(os.getenv("SUCCESS_RETENTION_DAYS", "7"))
UPLOAD_SCANNER = os.getenv("UPLOAD_SCANNER", "disabled" if DEBUG else "clamav")
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "scanner")
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
MAIL_DELIVERY_MODE = os.getenv("MAIL_DELIVERY_MODE", "local" if DEBUG else "mailersend")
MAILERSEND_API_TOKEN = os.getenv("MAILERSEND_API_TOKEN", "")
MAILERSEND_WEBHOOK_SIGNING_SECRET = os.getenv("MAILERSEND_WEBHOOK_SIGNING_SECRET", "")
MAIL_FROM_ADDRESS = os.getenv("MAIL_FROM_ADDRESS", "reimbursement@example.invalid" if DEBUG else "")
MAIL_REPLY_TO_ADDRESS = os.getenv("MAIL_REPLY_TO_ADDRESS", MAIL_FROM_ADDRESS)
APPROVED_DELIVERY_RECIPIENTS = list(
    filter(
        None,
        os.getenv("APPROVED_DELIVERY_RECIPIENTS", "finance@example.invalid" if DEBUG else "")
        .lower()
        .split(","),
    )
)
TEST_RECIPIENT_ALLOWLIST = list(
    filter(None, os.getenv("TEST_RECIPIENT_ALLOWLIST", "").lower().split(","))
)
DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "development" if DEBUG else "production")
HEALTHCHECK_TOKEN = os.getenv("HEALTHCHECK_TOKEN", "")
REDACTED_LOG_FORMAT = "{levelname} {name} {message}"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"safe": {"format": REDACTED_LOG_FORMAT, "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "safe"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "CRITICAL", "propagate": False},
        "httpx": {"level": "WARNING"},
    },
}
if not DEBUG:
    required = [
        "DJANGO_SECRET_KEY",
        "DATABASE_URL",
        "ALLOWED_HOSTS",
        "MAILERSEND_API_TOKEN",
        "MAILERSEND_WEBHOOK_SIGNING_SECRET",
        "MAIL_FROM_ADDRESS",
        "APPROVED_DELIVERY_RECIPIENTS",
        "HEALTHCHECK_TOKEN",
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing or SECRET_KEY.startswith("development-"):
        raise ImproperlyConfigured(f"Missing production configuration: {', '.join(missing)}")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("Production requires PostgreSQL.")
    if UPLOAD_SCANNER != "clamav" or MAIL_DELIVERY_MODE != "mailersend":
        raise ImproperlyConfigured("Production requires ClamAV and MailerSend.")
    if DEPLOYMENT_ENV == "staging" and not TEST_RECIPIENT_ALLOWLIST:
        raise ImproperlyConfigured("Staging requires TEST_RECIPIENT_ALLOWLIST.")
