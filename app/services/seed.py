"""Тестовые участники для разработки и проверки тарифов. Команды не работают в production."""

from flask import current_app

from ..extensions import db
from ..models import Profile, User
from ..utils import utcnow
from .consents import record_consent

DOMAIN = "club.test"  # зарезервированный домен для тестов, письма туда не уйдут

# (логин, тариф, проверка личности, админ)
TEST_USERS = (
    ("guest", "guest", True, False),
    ("starter", "starter", True, False),
    ("growth", "growth", True, False),
    ("premium", "premium", True, False),
    ("unverified", "guest", False, False),
    ("admin", "premium", True, True),
)


class SeedError(Exception):
    pass


def check_allowed(seed_password):
    if current_app.config.get("APP_ENV") == "production":
        raise SeedError("Тестовых участников нельзя создавать в production.")
    if not seed_password or len(seed_password) < 10:
        raise SeedError("TEST_SEED_PASSWORD в .env не задан или короче 10 символов.")


def seed_test_users(seed_password):
    """Создаёт или обновляет тестовых участников. Повторный запуск ничего не ломает."""
    check_allowed(seed_password)
    results = []
    for login, plan, verified, admin in TEST_USERS:
        email = f"{login}@{DOMAIN}"
        user = User.query.filter_by(email=email).first()
        created = user is None
        if created:
            user = User(email=email, first_name=login.capitalize(), last_name="Test")
            db.session.add(user)
            user.profile = Profile(residence_country="DE")
            for kind in ("privacy", "terms", "identity"):
                record_consent(user, kind, True, source="seed")
        user.set_password(seed_password)
        user.plan = plan
        user.is_admin = admin
        user.status = "active"
        user.email_confirmed_at = user.email_confirmed_at or utcnow()
        if verified:
            if not user.is_verified:
                user.verified_at = utcnow()
            user.verification_status = "verified"
            user.verification_reason = None
        else:
            user.verification_status = "none"
            user.verified_at = None
        results.append((email, plan, "создан" if created else "обновлён", admin, verified))
    db.session.commit()
    return results
