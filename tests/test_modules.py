from app import create_app
from app.extensions import db
from app.models import Profile, User
from config.plans import GUEST_FEATURES, PLANS, PREMIUM_FEATURES, STARTER_FEATURES, GROWTH_FEATURES
from app.modules import enabled_modules
from tests.conftest import PASSWORD, login, make_user


def test_plans_grow_upward_only():
    """Каждый следующий тариф включает всё из предыдущего."""
    assert GUEST_FEATURES < STARTER_FEATURES < GROWTH_FEATURES < PREMIUM_FEATURES


def test_tier_features_match_the_agreed_layout():
    assert "startups.publish" not in STARTER_FEATURES
    assert "startups.publish" in GROWTH_FEATURES
    assert "auction.access" not in GROWTH_FEATURES
    assert "auction.access" in PREMIUM_FEATURES
    assert "services.use" in STARTER_FEATURES
    assert "services.use" not in GUEST_FEATURES
    assert set(PLANS) == {"guest", "starter", "growth", "premium"}


def test_enabled_modules_follow_the_setting():
    keys = [m.key for m in enabled_modules(["auction", "profile"])]
    assert keys == ["profile", "auction"]  # порядок реестра, а не порядок настройки


def test_guest_can_write_about_self(app, client):
    user_id = make_user(app, verified=True)
    login(client)

    response = client.post("/profile/", data={"headline": "Designer", "bio": "Hello club"})

    assert response.status_code == 302
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        assert profile.headline == "Designer"
        assert profile.bio == "Hello club"


def test_profile_rejects_too_long_headline(app, client):
    make_user(app, verified=True)
    login(client)

    response = client.post("/profile/", data={"headline": "x" * 161, "bio": ""})

    assert response.status_code == 200
    assert "Bitte prüfe diese Felder: headline" in response.get_data(as_text=True)


def test_guest_is_redirected_from_paid_modules(app, client):
    make_user(app, verified=True)
    login(client)

    # Просмотр открыт всем верифицированным, платное — только сервисы и аукцион
    assert client.get("/startups/").status_code == 200
    assert client.get("/travel/").status_code == 200
    for path in ("/services/", "/auction/"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers["Location"].endswith("/app/"), path


def test_starter_reaches_services_and_can_read_startups(app, client):
    make_user(app, verified=True, plan="starter")
    login(client)

    assert client.get("/services/").status_code == 200
    assert client.get("/travel/").status_code == 200
    assert client.get("/startups/").status_code == 200  # читать может каждый, участвовать — по тарифу


def test_growth_reaches_startups_but_not_auction(app, client):
    make_user(app, verified=True, plan="growth")
    login(client)

    assert client.get("/startups/new").status_code == 200
    assert client.get("/auction/").status_code == 302


def test_premium_reaches_auction(app, client):
    make_user(app, verified=True, plan="premium")
    login(client)

    assert client.get("/auction/").status_code == 200


def test_unverified_user_cannot_use_modules_even_with_paid_plan(app, client):
    make_user(app, verified=False, plan="premium")
    login(client)

    response = client.get("/auction/")

    assert response.status_code == 302


def test_dashboard_lists_only_available_modules_as_links(app, client):
    make_user(app, verified=True, plan="starter")
    login(client)

    body = client.get("/app/").get_data(as_text=True)

    assert 'href="/services/"' in body
    assert 'href="/auction/"' not in body
    assert "nicht im Tarif enthalten" in body


def test_navigation_shows_only_allowed_modules(app, client):
    make_user(app, verified=True, plan="guest")
    login(client)

    body = client.get("/app/").get_data(as_text=True)

    assert 'href="/profile/"' in body
    assert 'href="/services/"' not in body


def test_disabled_module_is_not_registered(monkeypatch):
    from config import plans  # noqa: F401  (импорт проверяет, что конфиг подгружается)
    from app.config import TestConfig

    monkeypatch.setattr(TestConfig, "ENABLED_MODULES", ["profile"])
    application = create_app("test")

    client = application.test_client()
    assert client.get("/services/").status_code == 404
    assert client.get("/auction/").status_code == 404
    assert "profile.edit" in application.view_functions
