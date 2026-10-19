import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Consent, Profile, User


def test_user_defaults(app):
    with app.app_context():
        user = User(email="a@example.com")
        db.session.add(user)
        db.session.commit()

        assert user.locale == "de"
        assert user.timezone == "Europe/Berlin"
        assert user.verification_status == "none"
        assert user.verified_at is None
        assert user.created_at is not None


def test_invalid_verification_status_is_rejected(app):
    with app.app_context():
        db.session.add(User(email="b@example.com", verification_status="maybe"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_profile_and_consent_link_to_user(app):
    with app.app_context():
        user = User(email="c@example.com")
        user.profile = Profile(display_name="Test", country="DE", residence_country="DE")
        user.consents.append(Consent(kind="terms", document_version="1.0"))
        db.session.add(user)
        db.session.commit()

        loaded = db.session.get(User, user.id)
        assert loaded.profile.residence_country == "DE"
        assert [c.kind for c in loaded.consents] == ["terms"]


def test_migrations_match_models(app):
    """Миграции должны описывать ровно текущие модели: иначе схема в проде разойдётся с кодом."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with app.app_context():
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, db.metadata)

    assert diff == [], f"Схема БД расходится с моделями: {diff}"
