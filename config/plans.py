"""Матрица тарифов: какие функции доступны на каком тарифе.

Названия тарифов и состав окончательно не утверждены: владельцы обсудят цены позже.
Каждый тариф наследует функции предыдущего (см. _inherit ниже), поэтому тарифы растут только вверх.
Доступ проверяет services/entitlements.py, а не условия в шаблонах.
"""


def _inherit(base, *extra):
    return frozenset(base) | frozenset(extra)


GUEST_FEATURES = frozenset({"dashboard", "profile.edit", "community.read", "assistant.use"})
STARTER_FEATURES = _inherit(GUEST_FEATURES, "services.use", "travel.book", "events.join", "events.create")
GROWTH_FEATURES = _inherit(STARTER_FEATURES, "startups.publish", "startups.interest", "travel.offer")
PREMIUM_FEATURES = _inherit(GROWTH_FEATURES, "auction.access")

PLANS = {
    "guest": {"name": "Gast", "price_eur_cents": 0, "features": GUEST_FEATURES},
    "starter": {"name": "Starter", "price_eur_cents": None, "features": STARTER_FEATURES},
    "growth": {"name": "Growth", "price_eur_cents": None, "features": GROWTH_FEATURES},
    "premium": {"name": "Premium", "price_eur_cents": None, "features": PREMIUM_FEATURES},
}

DEFAULT_PLAN = "guest"

# Запусков встроенных инструментов в календарный месяц. Ключи — как в app/services/tools/__init__.py.
# Лимиты растут вместе с тарифом; цифры предварительные, владельцы утвердят вместе с ценами.
TOOL_LIMITS = {
    "starter": {"seo": 5, "compliance": 5, "security": 5, "laws": 20, "panel": 1},
    "growth": {"seo": 10, "compliance": 10, "security": 10, "laws": 40, "panel": 3},
    "premium": {"seo": 30, "compliance": 30, "security": 30, "laws": 100, "panel": 10},
}

# Вопросов личному помощнику Clubi в сутки. Цифры предварительные, как и цены.
ASSISTANT_DAILY = {"guest": 15, "starter": 40, "growth": 80, "premium": 200}
