"""Матрица тарифов: какие функции доступны на каком тарифе.

Цены утверждены владельцами (30.10.2026): помесячно и за год (год = 10 месяцев).
Названия и состав тарифов могут меняться; stripe-setup создаёт новую цену в Stripe, если сумма изменилась.
Каждый тариф наследует функции предыдущего (см. _inherit ниже), поэтому тарифы растут только вверх.
Доступ проверяет services/entitlements.py, а не условия в шаблонах.
"""

from flask_babel import lazy_gettext as _l


def _inherit(base, *extra):
    return frozenset(base) | frozenset(extra)


GUEST_FEATURES = frozenset({"dashboard", "profile.edit", "community.read", "assistant.use"})
STARTER_FEATURES = _inherit(GUEST_FEATURES, "services.use", "travel.book", "events.join", "events.create",
                           "miniclubs.join", "miniclubs.create")
GROWTH_FEATURES = _inherit(STARTER_FEATURES, "startups.publish", "startups.interest", "travel.offer")
PREMIUM_FEATURES = _inherit(GROWTH_FEATURES, "auction.access")

PLANS = {
    "guest": {"name": _l("Guest"), "price_eur_cents": 0, "features": GUEST_FEATURES},
    "starter": {"name": _l("Starter"), "price_eur_cents": 1900, "price_year_eur_cents": 19000, "features": STARTER_FEATURES},
    "growth": {"name": _l("Growth"), "price_eur_cents": 4900, "price_year_eur_cents": 49000, "features": GROWTH_FEATURES},
    "premium": {"name": _l("Premium"), "price_eur_cents": 9900, "price_year_eur_cents": 99000, "features": PREMIUM_FEATURES},
}

DEFAULT_PLAN = "guest"

# Платные тарифы по возрастанию и периоды оплаты (интервалы Stripe)
PAID_PLANS = ("starter", "growth", "premium")
INTERVALS = ("month", "year")


def price_cents(plan: str, interval: str) -> int:
    """Цена тарифа в центах за период оплаты."""
    data = PLANS[plan]
    return data["price_year_eur_cents"] if interval == "year" else data["price_eur_cents"]

# Запусков встроенных инструментов в календарный месяц. Ключи — как в app/services/tools/__init__.py.
# Лимиты растут вместе с тарифом; цифры предварительные, владельцы утвердят вместе с ценами.
TOOL_LIMITS = {
    "starter": {"seo": 5, "compliance": 5, "security": 5, "laws": 20, "panel": 1},
    "growth": {"seo": 10, "compliance": 10, "security": 10, "laws": 40, "panel": 3},
    "premium": {"seo": 30, "compliance": 30, "security": 30, "laws": 100, "panel": 10},
}

# Вопросов личному помощнику Clubi в сутки. Цифры предварительные, как и цены.
ASSISTANT_DAILY = {"guest": 15, "starter": 40, "growth": 80, "premium": 200}
