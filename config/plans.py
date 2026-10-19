"""Матрица тарифов. Состав и цены тарифов ещё обсуждаются с владельцами.

Сейчас описан только гостевой тариф: бесплатный, с ограниченными правами.
Доступ проверяет services/entitlements.py, а не разбросанные условия в шаблонах.
"""

PLANS = {
    "guest": {
        "name": "Gast",
        "price_eur_cents": 0,
        "features": frozenset({"dashboard", "community.read"}),
    },
}

DEFAULT_PLAN = "guest"
