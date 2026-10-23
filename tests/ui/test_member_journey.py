"""Database-free UI checks. Run with pytest --confcutdir=tests/ui tests/ui."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import render_template
from babel.messages.pofile import read_po

from app import create_app
from app.profile_guide import matchable, missing_for_matching


@pytest.fixture(scope="module")
def app():
    return create_app("test")


def test_public_journey_and_csp(app):
    response = app.test_client().get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'href="#main-content"' in body
    assert 'id="main-content" tabindex="-1"' in body
    for anchor in ("how-it-works", "features", "plans"):
        assert f'href="#{anchor}"' in body
        assert f'id="{anchor}"' in body
    assert 'aria-controls="primary-navigation"' in body
    assert 'data-motion-toggle' in body
    assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("verified,enabled,ready,filled,expected", [
    (True, True, False, False, "profile/"),
    (True, True, False, True, "profile/privacy"),
    (True, True, True, True, "profile/matches"),
    (False, True, False, False, None),
    (True, False, False, False, None),
])
def test_journey_respects_verification_and_module(app, verified, enabled, ready, filled, expected):
    user = SimpleNamespace(first_name="Test", verification_status="verified" if verified else "pending",
                           is_verified=verified, email_confirmed_at=True)
    with app.test_request_context("/app/"):
        body = render_template("member/dashboard.html", current_user=user, plan_name="Gast",
                               publication_decided=True, profile_enabled=enabled, matching_ready=ready,
                               matching_fields_ready=filled, modules=[], reason=None)
    assert ('id="journey-title"' in body) == bool(expected)
    if expected:
        assert f'class="button" href="/{expected}"' in body


@pytest.mark.parametrize("offer,need,challenge,expected", [
    ("Offer", "Need", "", []),
    ("Offer", "", "Challenge", []),
    ("", "Need", "", ["q_can_help"]),
    ("Offer", "  ", "", ["q_looking_for"]),
    ("", "", "", ["q_can_help", "q_looking_for"]),
])
def test_matching_hints_follow_actual_requirements(offer, need, challenge, expected):
    profile = SimpleNamespace(q_can_help=offer, q_looking_for=need, q_challenge=challenge, allow_matching=True)
    assert missing_for_matching(profile) == expected
    assert matchable(profile) == (not expected)


def test_german_catalog_complete():
    root = Path(__file__).resolve().parents[2]
    with (root / "translations/messages.pot").open("rb") as f:
        template = read_po(f)
    with (root / "translations/de/LC_MESSAGES/messages.po").open("rb") as f:
        catalog = read_po(f, locale="de")
    assert not [m.id for m in template if m.id and (
        not catalog.get(m.id) or not catalog.get(m.id).string or catalog.get(m.id).fuzzy)]


def test_templates_compile(app):
    for name in app.jinja_env.list_templates():
        app.jinja_env.get_template(name)
