"""Проверки безопасности: подтверждённая почта для всех, лимит входа по адресу почты, кеш страниц вошедших."""

import pytest

from app.extensions import db
from app.models import User
from app.utils import utcnow
from tests.conftest import login, make_user


def test_verified_identity_without_confirmed_email_does_not_open_the_club(app, client):
    uid = make_user(app, "member@example.com", verified=True, confirmed=False)
    with app.app_context():
        assert db.session.get(User, uid).is_verified is False
    login(client)
    assert client.get("/profile/").status_code == 302  # на панель, где просят подтвердить почту
    with app.app_context():
        user = db.session.get(User, uid)
        user.email_confirmed_at = utcnow()
        db.session.commit()
        assert user.is_verified is True


def test_unconfirmed_members_are_not_listed_or_matched(app):
    from app.services.community import visible_query

    make_user(app, "ok@example.com", verified=True)
    hidden = make_user(app, "hidden@example.com", verified=True, confirmed=False)
    with app.app_context():
        for uid in User.query.with_entities(User.id).all():
            profile = db.session.get(User, uid[0]).profile
            profile.visible_in_directory = True
        db.session.commit()
        ids = {user.id for user in visible_query().all()}
        assert hidden not in ids and len(ids) == 1


def test_login_limit_key_is_the_normalized_address_not_the_visitor_ip(app):
    from app.blueprints.auth import _login_email_key

    with app.test_request_context("/login", method="POST", data={"email": " Member@Example.com "},
                                  headers={"CF-Connecting-IP": "203.0.113.1"}):
        first = _login_email_key()
    with app.test_request_context("/login", method="POST", data={"email": "member@example.com"},
                                  headers={"CF-Connecting-IP": "198.51.100.9"}):
        assert _login_email_key() == first == "login:member@example.com"


def test_pages_of_logged_in_members_are_not_cached(app, client):
    make_user(app, "member@example.com", verified=True)
    assert "no-store" not in client.get("/login").headers.get("Cache-Control", "")
    login(client)
    assert "no-store" in client.get("/app/").headers.get("Cache-Control", "")
