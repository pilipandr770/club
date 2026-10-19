from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Consent, Profile, User


def _user(email, **extra):
    user = User(email=email, first_name="Test", **extra)
    user.set_password("correct-horse-battery")
    return user


def test_user_defaults(app):
    with app.app_context():
        user = _user("a@example.com")
        db.session.add(user)
        db.session.commit()

        assert user.locale == "de"
        assert user.timezone == "Europe/Berlin"
        assert user.plan == "guest"
        assert user.status == "active"
        assert user.verification_status == "none"
        assert user.verification_attempts == 0
        assert user.verified_at is None
        assert user.created_at is not None
        assert user.check_password("correct-horse-battery")
        assert user.is_verified is False


def test_invalid_verification_status_is_rejected(app):
    with app.app_context():
        db.session.add(_user("b@example.com", verification_status="maybe"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_profile_and_consent_link_to_user(app):
    with app.app_context():
        user = _user("c@example.com")
        user.profile = Profile(country="DE", residence_country="DE")
        user.consents.append(Consent(kind="terms", granted=True, version="2026-10-19"))
        db.session.add(user)
        db.session.commit()

        loaded = db.session.get(User, user.id)
        assert loaded.profile.residence_country == "DE"
        assert [(c.kind, c.granted) for c in loaded.consents] == [("terms", True)]


def test_migrations_match_models(app):
    """Схема, которую строят миграции, должна совпадать с моделями. Иначе прод разойдётся с кодом."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from flask_migrate import upgrade
    from sqlalchemy import text

    root = Path(__file__).resolve().parent.parent
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()
        try:
            upgrade(directory=str(root / "migrations"))
            with db.engine.connect() as connection:
                diff = compare_metadata(MigrationContext.configure(connection), db.metadata)
        finally:
            # Возвращаем схему из моделей, как её ожидают остальные тесты
            db.session.remove()
            db.drop_all()
            db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
            db.session.commit()
            db.create_all()

    assert diff == [], f"Схема из миграций расходится с моделями: {diff}"
