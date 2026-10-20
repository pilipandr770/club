from app.extensions import db
from app.models import Profile, User
from app.services.seed import DEMO_PROFILES, seed_demo_profiles
from tests.conftest import login

SEED_PASSWORD = "seed-password-123"


def test_demo_profiles_are_filled_visible_and_matchable(app, client):
    with app.app_context():
        seed_demo_profiles(SEED_PASSWORD)
        profiles = Profile.query.join(User).filter(User.email.like("demo-%")).all()
        assert len(profiles) == len(DEMO_PROFILES)
        for profile in profiles:
            assert profile.headline and profile.q_can_help and profile.q_looking_for
            assert profile.visible_in_directory and profile.allow_matching
            assert profile.embed_need is not None


def test_demo_seed_is_idempotent(app):
    with app.app_context():
        seed_demo_profiles(SEED_PASSWORD)
        seed_demo_profiles(SEED_PASSWORD)
        assert User.query.filter(User.email.like("demo-%")).count() == len(DEMO_PROFILES)


def test_demo_member_appears_in_directory_for_members(app, client):
    with app.app_context():
        seed_demo_profiles(SEED_PASSWORD)
    from tests.conftest import make_user
    make_user(app, email="viewer@example.com", verified=True)
    login(client, "viewer@example.com")

    body = client.get("/profile/directory").get_data(as_text=True)

    assert "Lena" in body and "Mira" in body


def test_demo_members_are_labelled_as_demo(app):
    with app.app_context():
        seed_demo_profiles()
        names = [u.first_name for u in User.query.filter(User.email.like("demo-%")).all()]
        assert names and all("(Demo)" in name for name in names)


def test_demo_seed_refuses_production_without_flag(app):
    import pytest
    from app.services.seed import SeedError

    app.config["APP_ENV"] = "production"
    try:
        with app.app_context():
            with pytest.raises(SeedError):
                seed_demo_profiles()
    finally:
        app.config["APP_ENV"] = "test"
