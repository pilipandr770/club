import os


def _database_url():
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://club:club@localhost:5432/club",
    )


class Config:
    """Базовая конфигурация. Секреты только из окружения (.env), в коде значений нет."""

    APP_ENV = os.environ.get("APP_ENV", "development")
    SECRET_KEY = os.environ.get("SECRET_KEY", "")

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Язык: в интерфейсе включён только de (см. ARCHITECTURE.md §3)
    LOCALES_ENABLED = ["de"]
    DEFAULT_LOCALE = "de"
    DEFAULT_TIMEZONE = "Europe/Berlin"

    # Юрисдикция по умолчанию для юридических инструментов (ARCHITECTURE.md §4)
    DEFAULT_JURISDICTION = "DE"

    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True

    BABEL_DEFAULT_LOCALE = DEFAULT_LOCALE
    BABEL_DEFAULT_TIMEZONE = DEFAULT_TIMEZONE
    # Каталоги лежат в корне проекта, а не внутри пакета app/
    BABEL_TRANSLATION_DIRECTORIES = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "translations")
    )


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")


class ProductionConfig(Config):
    DEBUG = False


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://club:club@localhost:5432/club_test",
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "test": TestConfig,
}
