"""Сообщество: каталог с поиском и фильтрами, карточка-минисайт, настройки карточки, приватность ссылок."""

from app.extensions import db
from app.models import User
from tests.conftest import login, make_user
from tests.test_matching import _member


def _setup(app):
    me = _member(app, "me@example.com", headline="Tax adviser", city="Frankfurt", industry="Finance",
                 expertise="tax, bookkeeping")
    other = _member(app, "other@example.com", headline="Web developer", city="Offenbach", industry="Software",
                    expertise="python, react", open_to="clients,partners",
                    website_url="https://dev.example.org", linkedin_url="https://linkedin.example/dev")
    hidden = _member(app, "hidden@example.com", headline="Hidden person", visible_in_directory=False)
    return me, other, hidden


def test_directory_lists_only_visible_members(app, client):
    me, other, hidden = _setup(app)
    login(client, "me@example.com")
    page = client.get("/profile/directory").get_data(as_text=True)
    assert "Web developer" in page
    assert "Hidden person" not in page


def test_directory_search_and_filters(app, client):
    _setup(app)
    login(client, "me@example.com")
    assert "Web developer" in client.get("/profile/directory?q=python").get_data(as_text=True)
    assert "Web developer" not in client.get("/profile/directory?q=Tax+adviser").get_data(as_text=True)
    assert "Web developer" in client.get("/profile/directory?city=Offenbach").get_data(as_text=True)
    assert "Web developer" in client.get("/profile/directory?open_to=partners").get_data(as_text=True)
    # неизвестный ключ игнорируется (фильтр не применяется), а не ломает страницу
    assert client.get("/profile/directory?open_to=part").status_code == 200
    assert "Web developer" not in client.get("/profile/directory?open_to=hire").get_data(as_text=True)


def test_card_hides_links_until_contact_is_accepted(app, client):
    me, other, _ = _setup(app)
    login(client, "me@example.com")
    page = client.get(f"/profile/{other}").get_data(as_text=True)
    assert "Web developer" in page
    assert "dev.example.org" not in page and "other@example.com" not in page
    assert "Request contact" in page or "Kontakt anfragen" in page


def test_hidden_member_card_is_not_found(app, client):
    me, other, hidden = _setup(app)
    login(client, "me@example.com")
    assert client.get(f"/profile/{hidden}").status_code == 404


def test_save_card_validates_theme_open_to_and_links(app, client):
    me, other, _ = _setup(app)
    login(client, "me@example.com")
    client.post("/profile/card", data={"cover_theme": "evil", "open_to": ["clients", "bogus", "clients"],
                                       "languages": "German, English, german", "website_url": "javascript:alert(1)",
                                       "linkedin_url": "https://linkedin.example/me"}, follow_redirects=True)
    with app.app_context():
        profile = db.session.get(User, me).profile
        assert profile.cover_theme == "violet"
        assert profile.open_to == "clients"
        assert profile.languages == "German, English"
        assert profile.website_url is None
        assert profile.linkedin_url == "https://linkedin.example/me"


def test_card_edit_form_and_own_preview(app, client):
    me, _, _ = _setup(app)
    login(client, "me@example.com")
    assert 'name="cover_theme"' in client.get("/profile/").get_data(as_text=True)
    assert client.get(f"/profile/{me}").status_code == 200


# --- ссылки на соцсети ---------------------------------------------------------

def test_social_normalize_accepts_handle_and_checks_domain(app):
    from app.social import normalize
    assert normalize("telegram", "@anna") == "https://t.me/anna"
    assert normalize("github", "annadev") == "https://github.com/annadev"
    assert normalize("instagram", "https://www.instagram.com/anna/") == "https://www.instagram.com/anna/"
    assert normalize("instagram", "instagram.com/anna") == "https://instagram.com/anna"
    assert normalize("instagram", "https://evil.example/instagram.com") is None
    assert normalize("instagram", "https://instagram.com.evil.example/x") is None
    assert normalize("xing", "annadev") is None  # у сети нет короткого имени
    assert normalize("x", "javascript:alert(1)") is None
    assert normalize("nope", "https://x.com/a") is None


def test_save_card_stores_social_links_and_reports_bad_ones(app, client):
    me, _, _ = _setup(app)
    login(client, "me@example.com")
    response = client.post("/profile/card", data={"cover_theme": "violet", "social_telegram": "@anna",
                                                  "social_instagram": "https://evil.example/x"},
                           follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(User, me).profile.social_links == {"telegram": "https://t.me/anna"}


def test_social_links_visible_only_by_consent_or_accepted_contact(app, client):
    me, other, _ = _setup(app)
    with app.app_context():
        profile = db.session.get(User, other).profile
        profile.social_links = {"telegram": "https://t.me/devperson"}
        profile.links_public = False
        db.session.commit()
    login(client, "me@example.com")
    page = client.get(f"/profile/{other}").get_data(as_text=True)
    assert "t.me/devperson" not in page and "dev.example.org" not in page
    with app.app_context():
        db.session.get(User, other).profile.links_public = True
        db.session.commit()
    page = client.get(f"/profile/{other}").get_data(as_text=True)
    assert "t.me/devperson" in page and "dev.example.org" in page


def test_privacy_page_records_social_links_consent(app, client):
    me, _, _ = _setup(app)
    login(client, "me@example.com")
    client.post("/profile/privacy", data={"visible_in_directory": "1", "allow_matching": "1", "links_public": "1"})
    with app.app_context():
        from app.services.consents import latest_consent
        user = db.session.get(User, me)
        assert user.profile.links_public is True
        assert latest_consent(user, "social_links") is True
