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
