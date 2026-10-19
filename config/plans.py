"""Матрица тарифов: какие функции доступны на каком тарифе.

Названия тарифов и состав окончательно не утверждены: владельцы обсудят цены позже.
Каждый тариф наследует функции предыдущего (см. _inherit ниже), поэтому тарифы растут только вверх.
Доступ проверяет services/entitlements.py, а не условия в шаблонах.
"""


def _inherit(base, *extra):
    return frozenset(base) | frozenset(extra)


GUEST_FEATURES = frozenset({"dashboard", "profile.edit", "community.read"})
STARTER_FEATURES = _inherit(GUEST_FEATURES, "services.use", "travel.book")
GROWTH_FEATURES = _inherit(STARTER_FEATURES, "startups.publish")
PREMIUM_FEATURES = _inherit(GROWTH_FEATURES, "auction.access")

PLANS = {
    "guest": {"name": "Gast", "price_eur_cents": 0, "features": GUEST_FEATURES},
    "starter": {"name": "Starter", "price_eur_cents": None, "features": STARTER_FEATURES},
    "growth": {"name": "Growth", "price_eur_cents": None, "features": GROWTH_FEATURES},
    "premium": {"name": "Premium", "price_eur_cents": None, "features": PREMIUM_FEATURES},
}

DEFAULT_PLAN = "guest"
