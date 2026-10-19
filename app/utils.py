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


BERLIN = "Europe/Berlin"


def eur_to_cents(value):
    """'12,50' или '12.5' -> 1250. Бросает ValueError на мусор и отрицательные суммы."""
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid amount") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid or negative amount")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_eur(cents):
    if cents is None:
        return ""
    return f"{cents // 100},{cents % 100:02d} €"


def local_to_utc(value, tz=BERLIN):
    """'2026-10-25T18:00' (ввод в местном времени) -> naive UTC datetime. ValueError на мусор."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    local = _dt.fromisoformat(value.strip())
    aware = local.replace(tzinfo=ZoneInfo(tz))
    return aware.astimezone(UTC).replace(tzinfo=None)
