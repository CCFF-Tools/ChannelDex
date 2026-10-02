import os
import importlib.util
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("PUBTV_SECRET_KEY") or secrets.token_urlsafe(50)
DEBUG = os.environ.get("PUBTV_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = []
ROOT_URLCONF = "pubtv.config.urls"
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    ("whitenoise.middleware.WhiteNoiseMiddleware" if importlib.util.find_spec("whitenoise") else "pubtv.config.static.StaticFilesMiddleware"),
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.staticfiles", "django.contrib.messages", "django.forms", "pubtv.operations"]
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "pubtv" / "templates"],
    "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.messages.context_processors.messages", "pubtv.config.settings.quit_enabled"]},
}]

def quit_enabled(request):  # noqa: ARG001
    return {"quit_enabled": os.environ.get("PUBTV_ENABLE_QUIT") == "1"}
WSGI_APPLICATION = "pubtv.config.wsgi.application"
DATA_DIR = Path(os.environ["PUBTV_DATA_DIR"]) if os.environ.get("PUBTV_DATA_DIR") else BASE_DIR
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "pubtv.sqlite3"}}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Detroit"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "pubtv" / "static"]
STATIC_ROOT = DATA_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "pubtv.config.static.DevelopmentManifestStaticFilesStorage"},
}
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_000_000
