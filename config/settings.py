import os
from pathlib import Path

from dotenv import load_dotenv
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = 'django-insecure-ze%58%00w2rm6i_+pkrl%x31j45+!a1#t6h9)c^$0xkt-!j@e='

DEBUG = True

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "15.164.151.62",
    "feedit-official.duckdns.org",
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "django.contrib.humanize",
    'dashboard.apps.AdminDashboardConfig',
    "core",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'dashboard.middleware.DashboardCacheMiddleware',
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



DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "OPTIONS": {
            "options": '-c search_path=dictionary,"$user",public',
        },
    }
}


# Dashboard cache
# Celery broker=/0, result backend=/1과 분리해서 dashboard cache는 /2 사용.
DASHBOARD_CACHE_URL = os.getenv(
    "DASHBOARD_CACHE_URL",
    "redis://127.0.0.1:6379/2",
)

DASHBOARD_PAGE_CACHE_SECONDS = int(
    os.getenv("DASHBOARD_PAGE_CACHE_SECONDS", "120")
)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": DASHBOARD_CACHE_URL,
        "TIMEOUT": 300,
        "KEY_PREFIX": "feedit",
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

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


LLANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True


STATIC_URL = 'static/'


CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://127.0.0.1:6379/0",
)

CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://127.0.0.1:6379/1",
)

CELERY_ACCEPT_CONTENT = [
    "json",
]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = "Asia/Seoul"
CELERY_ENABLE_UTC = True


CELERY_TASK_TRACK_STARTED = True

CELERY_TASK_ACKS_LATE = True

CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_BEAT_SCHEDULE = {
    "dispatch-due-crawl-targets": {
        "task": "core.dispatch_due_targets",
        "schedule": 60.0,
    },
}

AWS_STORAGE_BUCKET_NAME = os.getenv(
    "AWS_STORAGE_BUCKET_NAME"
)

AWS_REGION = os.getenv(
    "AWS_REGION",
    "ap-northeast-2",
)

KURE_HIGH_CONFIDENCE_THRESHOLD = (
    float(os.getenv("KURE_HIGH_CONFIDENCE_THRESHOLD"))
    if os.getenv("KURE_HIGH_CONFIDENCE_THRESHOLD")
    else None
)
KURE_HIGH_CONFIDENCE_MARGIN_THRESHOLD = (
    float(os.getenv("KURE_HIGH_CONFIDENCE_MARGIN_THRESHOLD"))
    if os.getenv("KURE_HIGH_CONFIDENCE_MARGIN_THRESHOLD")
    else None
)

CELERY_BEAT_SCHEDULE = {
    "dispatch-due-crawl-targets": {
        "task": "core.dispatch_due_targets",
        "schedule": crontab(
            minute="*",
        ),
        "kwargs": {
            "batch_size": 2,
        },
    },
}