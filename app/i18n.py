"""Выбор языка и часового пояса (ARCHITECTURE.md §3, §5).

Порядок выбора языка: user.locale → cookie → лучшее совпадение Accept-Language → default.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, request
from flask_login import current_user
from flask_babel import lazy_gettext as _l  # noqa: F401  (реэкспорт для моделей и реестров)


def pick_locale(user_locale, cookie_locale, accept_languages, enabled, default):
    """Чистая функция выбора языка — удобна для тестов."""
    for candidate in (user_locale, cookie_locale):
        if candidate and candidate in enabled:
            return candidate
    if accept_languages is not None:
        best = accept_languages.best_match(enabled)
        if best:
            return best
    return default


def select_locale():
    enabled = current_app.config["LOCALES_ENABLED"]
    default = current_app.config["DEFAULT_LOCALE"]
    user = current_user
    return pick_locale(
        getattr(user, "locale", None),
        request.cookies.get("locale"),
        request.accept_languages,
        enabled,
        default,
    )


def resolve_timezone(name, default):
    """Возвращает пояс по имени или значение по умолчанию, если имя неизвестно."""
    if not name:
        return default
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return default
    return name


def select_timezone():
    default = current_app.config["DEFAULT_TIMEZONE"]
    user = current_user
    return resolve_timezone(getattr(user, "timezone", None), default)
