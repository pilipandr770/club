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

    # Версия текстов согласий (записывается в каждое согласие)
    CONSENT_VERSION = "2026-10-19"

    # Проверка личности (ARCHITECTURE.md §10, §11)
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    IDENTITY_MAX_ATTEMPTS = 3
    BLOCKED_DOCUMENT_COUNTRIES = _csv("BLOCKED_DOCUMENT_COUNTRIES", DEFAULT_BLOCKED_COUNTRIES)
    BLOCKED_RESIDENCE_COUNTRIES = _csv("BLOCKED_RESIDENCE_COUNTRIES", DEFAULT_BLOCKED_COUNTRIES)

    # ИИ-ассистент (Anthropic API): ключ только из .env
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")

    # Почта: при деплое. Без SMTP_HOST письма только пишутся в лог.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_TLS = os.environ.get("SMTP_TLS", "1") == "1"
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "club@localhost")

    RATELIMIT_STORAGE_URI = "memory://"


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
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://club:club@localhost:5432/club_test",
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "test": TestConfig,
}
