"""Список стран для формы регистрации: названия из Babel на языке интерфейса."""

from babel import Locale
from flask_babel import get_locale

# Коды, которые Babel относит к территориям, но которые не являются странами
NOT_COUNTRIES = {"EU", "EZ", "QO", "XA", "XB", "XK", "AC", "CP", "DG", "EA", "IC", "TA", "UN"}


def country_choices():
    """[(ISO-код, название)] по алфавиту названий."""
    locale = Locale.parse(str(get_locale() or "de"))
    choices = [
        (code, name)
        for code, name in locale.territories.items()
        if len(code) == 2 and code.isalpha() and code not in NOT_COUNTRIES
    ]
    return sorted(choices, key=lambda item: item[1])


def is_known_country(code: str) -> bool:
    return any(code == known for known, _ in country_choices())
