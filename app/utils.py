from datetime import UTC, datetime
from urllib.parse import urlsplit


def utcnow():
    """Время в БД — naive UTC (CLAUDE.md, раздел «Архитектура»)."""
    return datetime.now(UTC).replace(tzinfo=None)


ALLOWED_URL_SCHEMES = ("http", "https")


def clean_url(value):
    """Пропускает только http(s)-ссылки. Пользовательские URL обязательно проходят через эту функцию."""
    if not value:
        return None
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ALLOWED_URL_SCHEMES or not parts.netloc:
        return None
    return value
