from datetime import timedelta

from app.extensions import db
from app.models import AuditLog, Lot, User
from app.utils import utcnow
from tests.conftest import login, make_user


def _admin(app):
    return make_user(app, email="boss@example.com", verified=True, admin=True)


def test_non_admin_gets_404_on_admin_area(app, client):
    make_user(app, verified=True)
    login(client)

    for path in ("/admin/", "/admin/users", "/admin/lots", "/admin/audit"):
        assert client.get(path).status_code == 404, path


def test_anonymous_is_sent_to_login_for_admin(client):
    response = client.get("/admin/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_admin_sees_dashboard_and_user_list(app, client):
    _admin(app)
    make_user(app, email="member@example.com", verified=True, plan="starter")
    login(client, "boss@example.com")

    assert client.get("/admin/").status_code == 200
    body = client.get("/admin/users?q=member").get_data(as_text=True)
    assert "member@example.com" in body


def test_admin_changes_plan_and_writes_audit(app, client):
    _admin(app)
    member_id = make_user(app, email="member@example.com", verified=True)
    login(client, "boss@example.com")

    response = client.post(f"/admin/users/{member_id}", data={
        "plan": "premium", "role": "member", "status": "active",
        "verification_status": "verified", "verification_reason": "",
    })

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, member_id).plan == "premium"
        entry = AuditLog.query.filter_by(action="admin.user_updated").one()
        assert "plan guest->premium" in entry.details
        assert entry.target == f"user:{member_id}"


def test_admin_can_block_member_and_manual_verify(app, client):
    _admin(app)
    member_id = make_user(app, email="member@example.com", verified=False)
    login(client, "boss@example.com")

    client.post(f"/admin/users/{member_id}", data={
        "plan": "guest", "role": "member", "status": "blocked",
        "verification_status": "manual", "verification_reason": "needs_review",
    })

    with app.app_context():
        user = db.session.get(User, member_id)
        assert user.status == "blocked"
        assert user.verification_status == "manual"
        assert user.verification_reason == "needs_review"


def test_admin_cannot_block_or_remove_own_admin_rights(app, client):
    admin_id = _admin(app)
    login(client, "boss@example.com")

    client.post(f"/admin/users/{admin_id}", data={
        "plan": "premium", "role": "member", "status": "blocked",
        "verification_status": "verified", "verification_reason": "",
    })

    with app.app_context():
        admin = db.session.get(User, admin_id)
        assert admin.status == "active"
        assert admin.is_admin is True


def test_admin_grants_admin_to_member(app, client):
    _admin(app)
    member_id = make_user(app, email="member@example.com", verified=True)
    login(client, "boss@example.com")

    client.post(f"/admin/users/{member_id}", data={
        "plan": "guest", "role": "member", "status": "active", "is_admin": "1",
        "verification_status": "verified", "verification_reason": "",
    })

    with app.app_context():
        assert db.session.get(User, member_id).is_admin is True


def test_admin_creates_and_closes_auction_lot(app, client):
    _admin(app)
    login(client, "boss@example.com")

    client.post("/admin/lots", data={
        "title": "Test lamp", "description": "Vintage", "mode": "descending",
        "start_price": "100,00", "step": "5,00", "floor_price": "40,00",
        "drop_interval_minutes": "5", "opens_at": "2026-01-01T10:00", "ends_at": "2026-01-01T12:00",
    })

    with app.app_context():
        lot = Lot.query.one()
        assert lot.title == "Test lamp"
        assert lot.start_price_cents == 10000
        assert lot.floor_price_cents == 4000
        assert lot.drop_interval_seconds == 300
        lot_id = lot.id

    client.post(f"/admin/lots/{lot_id}/cancel")
    with app.app_context():
        assert db.session.get(Lot, lot_id).status == "closed"
        assert AuditLog.query.filter_by(action="auction.lot_cancelled").count() == 1


def test_lot_form_with_bad_amount_creates_nothing(app, client):
    _admin(app)
    login(client, "boss@example.com")

    client.post("/admin/lots", data={
        "title": "Broken", "mode": "ascending", "start_price": "abc", "step": "5",
        "opens_at": "2026-01-01T10:00", "ends_at": "2026-01-01T12:00",
    })

    with app.app_context():
        assert Lot.query.count() == 0


def test_audit_page_lists_admin_actions(app, client):
    _admin(app)
    member_id = make_user(app, email="member@example.com", verified=True)
    login(client, "boss@example.com")
    client.post(f"/admin/users/{member_id}", data={
        "plan": "starter", "role": "member", "status": "active",
        "verification_status": "verified", "verification_reason": "",
    })

    body = client.get("/admin/audit").get_data(as_text=True)

    assert "admin.user_updated" in body
    assert "boss@example.com" in body
