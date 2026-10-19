import pytest

from app.extensions import db
from app.models import User
from app.services.seed import SeedError, seed_test_users
from tests.conftest import login

SEED_PASSWORD = "seed-password-123"


def test_seed_creates_one_account_per_tier_with_shared_password(app, client):
    with app.app_context():
        rows = seed_test_users(SEED_PASSWORD)
        users = {u.email: u for u in User.query.all()}

    assert len(rows) == 6
    assert users["guest@club.test"].plan == "guest"
    assert users["starter@club.test"].plan == "starter"
    assert users["growth@club.test"].plan == "growth"
    assert users["premium@club.test"].plan == "premium"
    assert users["admin@club.test"].is_admin is True
    assert users["unverified@club.test"].is_verified is False
    assert users["guest@club.test"].is_verified is True
    assert all(u.check_password(SEED_PASSWORD) for u in users.values())


def test_seeded_member_can_log_in_and_sees_own_tier(app, client):
    with app.app_context():
        seed_test_users(SEED_PASSWORD)
    response = client.post("/login", data={"email": "starter@club.test", "password": SEED_PASSWORD})

    assert response.status_code == 302
    body = client.get("/app/").get_data(as_text=True)
    assert 'href="/services/"' in body
    assert 'href="/startups/"' not in body


def test_seed_is_idempotent(app):
    with app.app_context():
        seed_test_users(SEED_PASSWORD)
        seed_test_users(SEED_PASSWORD)
        assert User.query.count() == 6


def test_seed_refuses_short_password(app):
    with app.app_context():
        with pytest.raises(SeedError):
            seed_test_users("short")


def test_seed_refuses_production(app):
    app.config["APP_ENV"] = "production"
    try:
        with app.app_context():
            with pytest.raises(SeedError):
                seed_test_users(SEED_PASSWORD)
    finally:
        app.config["APP_ENV"] = "test"
