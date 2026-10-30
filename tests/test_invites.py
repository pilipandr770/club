"""Приглашения: админ ручается за человека, тот регистрируется без Stripe; напоминание о профиле."""

from datetime import timedelta

import pytest

from app.extensions import db
from app.models import AuditLog, Consent, Invite, User
from app.services import invites, onboarding
from app.utils import utcnow
from tests.conftest import PASSWORD, login, make_user


def _admin(app):
    return make_user(app, "admin@example.com", verified=True, admin=True)


def _invite(app, **kwargs):
    with app.app_context():
        creator = User.query.filter_by(email="admin@example.com").one()
        invite = invites.create(creator, **{"note": "Max, Geschäftspartner seit 2019", **kwargs})
        db.session.commit()
        return invite.code, invite.id


def _register(client, code, email="max@example.com", **extra):
    data = {"first_name": "Max", "email": email, "password": PASSWORD, "residence_country": "DE",
            "consent_privacy": "1", "consent_terms": "1", "invite": code, **extra}
    return client.post("/register", data=data)


def test_invited_person_is_verified_by_the_voucher_without_stripe(app, client):
    admin_id = _admin(app)
    code, invite_id = _invite(app)
    page = client.get(f"/join/{code}").get_data(as_text=True)
    assert 'name="invite"' in page and "consent_identity" not in page
    assert _register(client, code).status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="max@example.com").one()
        # личность подтверждена поручителем, но пока не подтверждена почта: доступа к клубу ещё нет
        assert user.verification_status == "verified" and not user.is_verified
        assert user.verification_method == "manual" and user.verified_by_id == admin_id
        assert "Geschäftspartner" in user.verification_note and user.invite_id == invite_id
        assert user.plan == "guest"
        assert {c.kind for c in Consent.query.filter_by(user_id=user.id)} == {"privacy", "terms"}
        assert db.session.get(Invite, invite_id).uses == 1
        actions = {row.action for row in AuditLog.query.all()}
        assert {"admin.invite_created", "admin.verified_manually", "invite.redeemed"} <= actions
        assert code not in "".join(str(row.details) + str(row.target) for row in AuditLog.query.all())


def test_invitation_can_include_a_paid_plan(app, client):
    _admin(app)
    code, _id = _invite(app, plan="starter", months=6)
    _register(client, code)
    with app.app_context():
        user = User.query.filter_by(email="max@example.com").one()
        assert user.plan == "starter" and user.paid_until > utcnow() + timedelta(days=170)
        assert user.effective_plan == "starter"


def test_single_use_invitation_cannot_be_used_twice(app, client):
    _admin(app)
    code, _id = _invite(app)
    _register(client, code)
    response = _register(app.test_client(), code, email="second@example.com")
    assert response.status_code == 200 and "nicht mehr gültig" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(email="second@example.com").first() is None


def test_multi_use_invitation_counts_uses(app, client):
    _admin(app)
    code, invite_id = _invite(app, max_uses=2)
    _register(client, code)
    _register(app.test_client(), code, email="two@example.com")
    assert _register(app.test_client(), code, email="three@example.com").status_code == 200
    with app.app_context():
        assert db.session.get(Invite, invite_id).uses == 2


def test_invitation_bound_to_an_address_rejects_other_addresses(app, client):
    _admin(app)
    code, _id = _invite(app, email="max@example.com")
    assert "andere E-Mail-Adresse" in _register(client, code, email="other@example.com").get_data(as_text=True)
    assert _register(client, code, email="MAX@example.com").status_code == 302


def test_expired_and_revoked_invitations_do_not_work(app, client):
    _admin(app)
    expired, expired_id = _invite(app)
    revoked, revoked_id = _invite(app)
    with app.app_context():
        db.session.get(Invite, expired_id).expires_at = utcnow() - timedelta(minutes=1)
        invites.revoke(User.query.filter_by(email="admin@example.com").one(), db.session.get(Invite, revoked_id))
        db.session.commit()
    for code in (expired, revoked, "does-not-exist"):
        assert client.get(f"/join/{code}").status_code == 302  # на обычную регистрацию


def test_without_invitation_the_identity_consent_is_still_required(app, client):
    response = client.post("/register", data={"first_name": "Max", "email": "max@example.com", "password": PASSWORD,
                                              "residence_country": "DE", "consent_privacy": "1", "consent_terms": "1"})
    assert response.status_code == 200 and 'name="consent_identity"' in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(email="max@example.com").first() is None


def test_blocked_residence_is_refused_even_with_an_invitation(app, client):
    _admin(app)
    code, _id = _invite(app)
    assert _register(client, code, residence_country="RU").status_code == 200


def test_invitation_needs_a_note_and_sane_limits(app):
    _admin(app)
    with app.app_context():
        creator = User.query.filter_by(email="admin@example.com").one()
        for kwargs in (dict(note="x"), dict(note="Max, Freund", plan="starter", months=0),
                       dict(note="Max, Freund", max_uses=0), dict(note="Max, Freund", days=5),
                       dict(note="Max, Freund", email="kein-mail")):
            with pytest.raises(invites.InviteError):
                invites.create(creator, **kwargs)


def test_admin_page_creates_lists_and_revokes(app, client):
    _admin(app)
    login(client, "admin@example.com")
    response = client.post("/admin/invites", data={"note": "Anna, Kollegin", "plan": "guest", "months": 6,
                                                   "max_uses": 1, "days": 30}, follow_redirects=True)
    assert response.status_code == 200
    page = client.get("/admin/invites").get_data(as_text=True)
    assert "Anna, Kollegin" in page and "/join/" in page
    with app.app_context():
        invite_id = Invite.query.one().id
    assert client.post(f"/admin/invites/{invite_id}/revoke").status_code == 302
    with app.app_context():
        assert db.session.get(Invite, invite_id).revoked_at is not None


def test_members_cannot_open_the_invitation_page(app, client):
    make_user(app, "member@example.com", verified=True)
    login(client, "member@example.com")
    assert client.get("/admin/invites").status_code == 404
    assert client.post("/admin/invites", data={"note": "Test, Freund"}).status_code == 404


# --------------------------------------------------------------------------- онбординг
def _old_member(app, email="new@example.com", hours=30):
    uid = make_user(app, email, verified=True)
    with app.app_context():
        db.session.get(User, uid).created_at = utcnow() - timedelta(hours=hours)
        db.session.commit()
    return uid


def test_one_reminder_for_an_incomplete_profile_after_a_day(app):
    uid = _old_member(app)
    with app.test_request_context():
        assert [u.id for u in onboarding.due()] == [uid]
        assert onboarding.run() == 1
        assert db.session.get(User, uid).onboarding_nudged_at is not None
        assert onboarding.run() == 0


def test_no_reminder_too_early_too_late_or_when_not_needed(app):
    _old_member(app, "early@example.com", hours=2)
    _old_member(app, "late@example.com", hours=24 * 20)
    ready = _old_member(app, "ready@example.com")
    with app.app_context():
        profile = db.session.get(User, ready).profile
        profile.q_can_help, profile.q_looking_for, profile.allow_matching = "Beratung", "Investoren", True
        db.session.commit()
        assert onboarding.due() == []


def test_reminder_mail_is_in_the_member_language_and_links_to_the_profile(app, monkeypatch):
    uid = _old_member(app)
    sent = []
    monkeypatch.setattr(onboarding, "send_mail", lambda to, subject, body: sent.append((to, subject, body)) or True)
    with app.app_context():
        db.session.get(User, uid).locale = "ru"
        db.session.commit()
    with app.test_request_context():
        onboarding.run()
    to, subject, body = sent[0]
    assert to == "new@example.com" and "профил" in subject.lower() and "http://localhost/profile/" in body
