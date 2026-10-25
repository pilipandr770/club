"""Ручное членство: подтверждение личности админом и оплаченный срок тарифа."""

from datetime import datetime, timedelta

from app.extensions import db
from app.models import AuditLog, User
from app.services import membership
from app.services.entitlements import has_feature
from app.utils import add_months, utcnow
from tests.conftest import login, make_user


def _admin(app, email="boss@example.com"):
    return make_user(app, email=email, verified=True, admin=True)


def test_add_months_clamps_day_and_crosses_year():
    assert add_months(datetime(2026, 8, 31), 6) == datetime(2027, 2, 28)
    assert add_months(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)


def test_admin_confirms_identity_with_note_and_it_is_logged(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", verified=False)
    login(client, "boss@example.com")

    # без пометки подтверждение не проходит
    client.post(f"/admin/users/{member}/verify-manually", data={"note": ""})
    with app.app_context():
        assert db.session.get(User, member).verification_status != "verified"

    client.post(f"/admin/users/{member}/verify-manually", data={"note": "Kollege seit fünf Jahren, persönlich getroffen"})
    with app.app_context():
        user = db.session.get(User, member)
        assert user.is_verified and user.verification_method == "manual"
        assert user.verified_by_id is not None and "Kollege" in user.verification_note
        entry = AuditLog.query.filter_by(action="admin.verified_manually").one()
        assert "Kollege" in entry.details


def test_admin_cannot_confirm_or_revoke_own_identity(app, client):
    boss = _admin(app)
    login(client, "boss@example.com")
    client.post(f"/admin/users/{boss}/revoke-verification", data={"note": "test test test"})
    with app.app_context():
        assert db.session.get(User, boss).is_verified  # осталась

    other = make_user(app, email="x@example.com", verified=False)
    with app.app_context():
        admin = db.session.get(User, boss)
        try:
            membership.verify_manually(admin, admin, "ich selbst bestätigt")
        except membership.MembershipError as exc:
            assert exc.code == "self"
        else:
            raise AssertionError("self verification must fail")
        assert db.session.get(User, other).verification_method is None


def test_revoking_verification_removes_access(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", plan="starter", verified=True)
    login(client, "boss@example.com")
    client.post(f"/admin/users/{member}/revoke-verification", data={"note": "Irrtum, Person nicht bekannt"})
    with app.app_context():
        user = db.session.get(User, member)
        assert not user.is_verified and user.verification_method is None
        assert not has_feature(user, "assistant.use")


def test_generic_form_cannot_set_verified(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", verified=False)
    login(client, "boss@example.com")
    client.post(f"/admin/users/{member}", data={"plan": "guest", "role": "member", "status": "active",
                                                "verification_status": "verified", "verification_reason": ""})
    with app.app_context():
        assert db.session.get(User, member).verification_status != "verified"


def test_grant_plan_for_six_months_and_extend(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", verified=True)
    login(client, "boss@example.com")
    client.post(f"/admin/users/{member}/grant-plan", data={"plan": "growth", "months": "6", "note": "Überweisung 24.10."})
    with app.app_context():
        user = db.session.get(User, member)
        first = user.paid_until
        assert user.plan == "growth" and user.effective_plan == "growth"
        assert add_months(utcnow(), 6) - first < timedelta(minutes=5)
        assert "Überweisung" in user.payment_note
    client.post(f"/admin/users/{member}/grant-plan", data={"plan": "growth", "months": "3", "note": "Verlängerung bar"})
    with app.app_context():
        assert db.session.get(User, member).paid_until == add_months(first, 3)
        assert AuditLog.query.filter_by(action="admin.plan_granted").count() == 2


def test_grant_plan_needs_note_valid_months_and_paid_plan(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", verified=True)
    login(client, "boss@example.com")
    for data in ({"plan": "growth", "months": "6", "note": ""}, {"plan": "growth", "months": "5", "note": "bar bezahlt"},
                 {"plan": "guest", "months": "6", "note": "bar bezahlt"}):
        client.post(f"/admin/users/{member}/grant-plan", data=data)
    with app.app_context():
        user = db.session.get(User, member)
        assert user.plan == "guest" and user.paid_until is None


def test_expired_plan_falls_back_to_guest(app):
    member = make_user(app, email="friend@example.com", verified=True, plan="growth")
    with app.app_context():
        user = db.session.get(User, member)
        assert has_feature(user, "startups.publish")  # без срока: без ограничения
        user.paid_until = utcnow() - timedelta(days=1)
        assert user.effective_plan == "guest"
        assert not has_feature(user, "startups.publish")
        assert has_feature(user, "assistant.use")  # гостевое остаётся
        user.paid_until = utcnow() + timedelta(days=1)
        assert user.effective_plan == "growth" and has_feature(user, "startups.publish")


def test_end_plan_returns_to_guest(app, client):
    _admin(app)
    member = make_user(app, email="friend@example.com", verified=True, plan="starter")
    login(client, "boss@example.com")
    client.post(f"/admin/users/{member}/end-plan", data={"note": "auf Wunsch gekündigt"})
    with app.app_context():
        user = db.session.get(User, member)
        assert user.plan == "guest" and user.paid_until is None


def test_dashboard_and_filters_show_expiring_and_waiting(app, client):
    _admin(app)
    soon = make_user(app, email="soon@example.com", verified=True, plan="starter")
    late = make_user(app, email="late@example.com", verified=True, plan="starter")
    gone = make_user(app, email="gone@example.com", verified=True, plan="starter")
    waiting = make_user(app, email="waiting@example.com", verified=False)
    with app.app_context():
        db.session.get(User, soon).paid_until = utcnow() + timedelta(days=5)
        db.session.get(User, late).paid_until = utcnow() + timedelta(days=90)
        db.session.get(User, gone).paid_until = utcnow() - timedelta(days=2)
        db.session.commit()
    login(client, "boss@example.com")
    assert "soon@example.com" in client.get("/admin/").get_data(as_text=True)
    expiring = client.get("/admin/users?show=expiring").get_data(as_text=True)
    assert "soon@example.com" in expiring and "late@example.com" not in expiring
    assert "gone@example.com" in client.get("/admin/users?show=expired").get_data(as_text=True)
    waiting_page = client.get("/admin/users?show=waiting").get_data(as_text=True)
    assert "waiting@example.com" in waiting_page and "late@example.com" not in waiting_page


def test_create_participant_requires_note_for_manual_identity_and_payment(app, client):
    _admin(app)
    login(client, "boss@example.com")
    base = {"email": "new@example.com", "first_name": "Neu", "residence_country": "DE", "plan": "starter",
            "verified": "1", "months": "6"}
    client.post("/admin/users/new", data=base)
    with app.app_context():
        assert User.query.filter_by(email="new@example.com").first() is None  # пометок нет
    client.post("/admin/users/new", data={**base, "verification_note": "Freund der Familie",
                                          "payment_note": "bar bezahlt"})
    with app.app_context():
        user = User.query.filter_by(email="new@example.com").one()
        assert user.is_verified and user.verification_method == "manual"
        assert user.plan == "starter" and user.paid_until is not None


def test_non_admin_cannot_use_membership_routes(app, client):
    member = make_user(app, email="plain@example.com", verified=True)
    other = make_user(app, email="other@example.com", verified=False)
    login(client, "plain@example.com")
    for route in ("verify-manually", "revoke-verification", "grant-plan", "end-plan"):
        assert client.post(f"/admin/users/{other}/{route}", data={"note": "bla bla bla"}).status_code == 404
    with app.app_context():
        assert not db.session.get(User, other).is_verified
    assert member
