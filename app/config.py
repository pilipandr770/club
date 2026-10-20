import os

DEFAULT_BLOCKED_COUNTRIES = ("RU", "BY", "CN", "CU", "IR", "KP", "SY")


def _csv(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _database_url():
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://club:club@localhost:5432/club",
    )


class Config:
    """Базовая конфигурация. Секреты только из окружения (.env), в коде значений нет."""

    APP_ENV = os.environ.get("APP_ENV", "development")
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    BASE_URL = os.environ.get("BASE_URL", "https://localhost")

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Язык: в интерфейсе включён только de (ARCHITECTURE.md §3)
    LOCALES_ENABLED = ["de"]
    DEFAULT_LOCALE = "de"
    DEFAULT_TIMEZONE = "Europe/Berlin"

    # Юрисдикция по умолчанию для юридических инструментов (ARCHITECTURE.md §4)
    DEFAULT_JURISDICTION = "DE"

    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True

    BABEL_DEFAULT_LOCALE = DEFAULT_LOCALE
    BABEL_DEFAULT_TIMEZONE = DEFAULT_TIMEZONE
    # Каталоги лежат в корне проекта, а не внутри пакета app/
    BABEL_TRANSLATION_DIRECTORIES = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "translations")
    )

    # Модули клуба, которые работают (см. app/modules/__init__.py). Список через запятую в .env.
    ENABLED_MODULES = _csv("ENABLED_MODULES", ("profile", "services", "travel", "startups", "auction"))

    # Версия текстов согласий (записывается в каждое согласие)
    CONSENT_VERSION = "2026-10-19"

    # Проверка личности (ARCHITECTURE.md §10, §11)
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    IDENTITY_MAX_ATTEMPTS = 3
    BLOCKED_DOCUMENT_COUNTRIES = _csv("BLOCKED_DOCUMENT_COUNTRIES", DEFAULT_BLOCKED_COUNTRIES)
    BLOCKED_RESIDENCE_COUNTRIES = _csv("BLOCKED_RESIDENCE_COUNTRIES", DEFAULT_BLOCKED_COUNTRIES)

    # ИИ: модели выбираются здесь, в .env. Код менять не нужно.
    # LLM_PROVIDER: anthropic | openai | google | mistral | openai_compatible | none
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
    LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")  # только для openai_compatible (локальный сервер)
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # для openai_compatible, если нужен
    # EMBEDDING_PROVIDER: lexical | openai | google | mistral | openai_compatible | none
    EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "lexical")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")
    EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "")
    EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
    # Ключи провайдеров: только из .env
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
    MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")

    # Почта: при деплое. Без SMTP_HOST письма только пишутся в лог.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_TLS = os.environ.get("SMTP_TLS", "1") == "1"
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "club@localhost")

    RATELIMIT_STORAGE_URI = "memory://"

    # Загрузки (фото профилей). Сейчас — локальный диск, позже — S3 (Cloudflare R2) при деплое.
    MEDIA_ROOT = os.environ.get("MEDIA_ROOT", os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads"))
    MEDIA_MAX_BYTES = 5 * 1024 * 1024
    MAX_CONTENT_LENGTH = MEDIA_MAX_BYTES + 512 * 1024  # общий предел запроса, включая форму


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")


class ProductionConfig(Config):
    DEBUG = False


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SECRET_KEY = "test-secret"
    BASE_URL = "http://localhost"
    RATELIMIT_ENABLED = False
    MEDIA_ROOT = os.environ.get("TEST_MEDIA_ROOT", "/tmp/club-test-media")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://club:club@localhost:5432/club_test",
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "test": TestConfig,
}
