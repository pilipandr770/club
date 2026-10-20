from app.extensions import db
from app.models import AuditLog, User
from tests.conftest import login, make_user


def _admin(app):
    return make_user(app, email="boss@example.com", verified=True, admin=True)


def test_admin_creates_participant_without_stripe(app, client):
    _admin(app)
    login(client, "boss@example.com")

    response = client.post("/admin/users/new", data={
        "email": "demo@example.com", "first_name": "Demo", "last_name": "Person",
        "residence_country": "DE", "plan": "growth", "password": "demo-password-123", "verified": "1",
    })

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="demo@example.com").one()
        assert user.plan == "growth"
        assert user.is_verified is True
        assert user.verification_reason == "manual_override"
        assert user.email_confirmed_at is not None
        assert user.check_password("demo-password-123")
        assert AuditLog.query.filter_by(action="admin.user_created").count() == 1


def test_admin_creation_generates_password_when_empty(app, client):
    _admin(app)
    login(client, "boss@example.com")

    client.post("/admin/users/new", data={
        "email": "auto@example.com", "first_name": "Auto", "residence_country": "DE", "plan": "guest",
    })

    with app.app_context():
        user = User.query.filter_by(email="auto@example.com").one()
        assert user.is_verified is False
        assert user.password_hash  # временный пароль задан, его показали один раз


def test_admin_creation_refuses_duplicate_email(app, client):
    make_user(app, email="taken@example.com", verified=True)
    _admin(app)
    login(client, "boss@example.com")

    client.post("/admin/users/new", data={
        "email": "taken@example.com", "first_name": "X", "residence_country": "DE", "plan": "guest",
    })

    with app.app_context():
        assert User.query.filter_by(email="taken@example.com").count() == 1


def test_admin_verifies_identity_manually_and_it_is_audited(app, client):
    member_id = make_user(app, email="member@example.com", verified=False)
    _admin(app)
    login(client, "boss@example.com")

    client.post(f"/admin/users/{member_id}/verify-manually")

    with app.app_context():
        user = db.session.get(User, member_id)
        assert user.is_verified is True
        assert user.verification_reason == "manual_override"
        assert AuditLog.query.filter_by(action="admin.verified_manually").count() == 1


def test_manual_plan_change_needs_no_payment(app, client):
    member_id = make_user(app, email="member@example.com", verified=True, plan="guest")
    _admin(app)
    login(client, "boss@example.com")

    client.post(f"/admin/users/{member_id}", data={
        "plan": "premium", "role": "member", "status": "active",
        "verification_status": "verified", "verification_reason": "",
    })

    with app.app_context():
        assert db.session.get(User, member_id).plan == "premium"


def test_non_admin_cannot_create_or_verify(app, client):
    member_id = make_user(app, email="member@example.com", verified=False)
    login(client, "member@example.com")

    assert client.get("/admin/users/new").status_code == 404
    client.post(f"/admin/users/{member_id}/verify-manually")
    with app.app_context():
        assert db.session.get(User, member_id).is_verified is False
