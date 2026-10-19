from app.extensions import db
from app.models import InvestorInterest, Startup
from app.services.startups import parse_startup_form
from tests.conftest import login, make_user

FORM = {
    "name": "Solar Shelf", "tagline": "Panels for balconies", "description": "We sell balcony solar kits.",
    "stage": "prototype", "category": "climate", "funding_goal": "250000",
    "website_url": "https://example.com",
}


def _startup(app, owner_id, status="approved", name="Solar Shelf"):
    with app.app_context():
        startup = Startup(owner_id=owner_id, name=name, tagline="t", description="d",
                          stage="prototype", category="climate", status=status)
        if status == "approved":
            from app.utils import utcnow
            startup.approved_at = utcnow()
        db.session.add(startup)
        db.session.commit()
        return startup.id


def _owner_and_investor(app):
    owner = make_user(app, email="founder@example.com", verified=True, plan="growth")
    investor = make_user(app, email="investor@example.com", verified=True, plan="growth")
    return owner, investor


# --- разбор формы -------------------------------------------------------------

def test_form_parses_amount_and_accepts_https_link():
    class Form(dict):
        def get(self, key, default=""):
            return dict.get(self, key, default)

    data, errors = parse_startup_form(Form(FORM))

    assert errors == []
    assert data["funding_goal_cents"] == 25000000
    assert data["website_url"] == "https://example.com"


def test_form_refuses_script_links_and_bad_category():
    class Form(dict):
        def get(self, key, default=""):
            return dict.get(self, key, default)

    bad = dict(FORM, website_url="javascript:alert(1)", category="lottery")
    _, errors = parse_startup_form(Form(bad))

    assert "website_url" in errors
    assert "category" in errors


# --- публикация и модерация --------------------------------------------------

def test_growth_member_publishes_and_it_waits_for_review(app, client):
    make_user(app, email="founder@example.com", verified=True, plan="growth")
    login(client, "founder@example.com")

    response = client.post("/startups/new", data=dict(FORM, csrf_token="x"))

    assert response.status_code == 302
    with app.app_context():
        startup = Startup.query.one()
        assert startup.status == "pending"
        assert startup.funding_goal_cents == 25000000


def test_pending_startup_is_not_in_catalog_for_others(app, client):
    owner, _ = _owner_and_investor(app)
    _startup(app, owner, status="pending")
    make_user(app, email="other@example.com", verified=True, plan="premium")
    login(client, "other@example.com")

    assert "Solar Shelf" not in client.get("/startups/").get_data(as_text=True)


def test_approved_startup_appears_in_catalog(app, client):
    owner, _ = _owner_and_investor(app)
    _startup(app, owner, status="approved")
    make_user(app, email="other@example.com", verified=True, plan="growth")
    login(client, "other@example.com")

    assert "Solar Shelf" in client.get("/startups/").get_data(as_text=True)


def test_starter_cannot_browse_or_publish(app, client):
    make_user(app, email="small@example.com", verified=True, plan="starter")
    login(client, "small@example.com")

    assert client.get("/startups/").status_code == 302
    assert client.get("/startups/new").status_code == 302


def test_admin_approves_and_owner_sees_reason_when_rejected(app, client):
    owner, _ = _owner_and_investor(app)
    pending_id = _startup(app, owner, status="pending", name="Pending One")
    rejected_id = _startup(app, owner, status="pending", name="Rejected One")
    make_user(app, email="boss@example.com", verified=True, admin=True)
    login(client, "boss@example.com")

    client.post(f"/admin/startups/{pending_id}/review", data={"decision": "approve"})
    client.post(f"/admin/startups/{rejected_id}/review", data={"decision": "reject", "reason": "Unclear model"})

    with app.app_context():
        assert db.session.get(Startup, pending_id).status == "approved"
        rejected = db.session.get(Startup, rejected_id)
        assert rejected.status == "rejected"
        assert rejected.rejection_reason == "Unclear model"


def test_editing_sends_startup_back_to_review(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")
    login(client, "founder@example.com")

    client.post(f"/startups/{startup_id}/edit", data=dict(FORM, name="Solar Shelf 2", csrf_token="x"))

    with app.app_context():
        startup = db.session.get(Startup, startup_id)
        assert startup.name == "Solar Shelf 2"
        assert startup.status == "pending"


def test_other_member_cannot_edit(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")
    make_user(app, email="other@example.com", verified=True, plan="growth")
    login(client, "other@example.com")

    assert client.get(f"/startups/{startup_id}/edit").status_code == 404


# --- интерес и контакты -----------------------------------------------------

def test_contact_is_hidden_until_founder_accepts(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")

    login(client, "investor@example.com")
    client.post(f"/startups/{startup_id}/interest", data={"message": "Let's talk"})
    assert "founder@example.com" not in client.get(f"/startups/{startup_id}").get_data(as_text=True)

    client.post("/logout")
    login(client, "founder@example.com")
    with app.app_context():
        interest = InvestorInterest.query.one()
        interest_id = interest.id
    client.post(f"/startups/interests/{interest_id}/respond", data={"answer": "accept"})

    client.post("/logout")
    login(client, "investor@example.com")
    assert "founder@example.com" in client.get(f"/startups/{startup_id}").get_data(as_text=True)


def test_declined_interest_keeps_contact_hidden(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")
    login(client, "investor@example.com")
    client.post(f"/startups/{startup_id}/interest", data={"message": "Hi"})
    client.post("/logout")

    login(client, "founder@example.com")
    with app.app_context():
        interest_id = InvestorInterest.query.one().id
    client.post(f"/startups/interests/{interest_id}/respond", data={"answer": "decline"})
    client.post("/logout")

    login(client, "investor@example.com")
    assert "founder@example.com" not in client.get(f"/startups/{startup_id}").get_data(as_text=True)
    with app.app_context():
        assert InvestorInterest.query.one().status == "declined"


def test_founder_cannot_express_interest_in_own_startup(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")
    login(client, "founder@example.com")

    client.post(f"/startups/{startup_id}/interest", data={"message": "me"})

    with app.app_context():
        assert InvestorInterest.query.count() == 0


def test_second_interest_from_same_investor_is_refused(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="approved")
    login(client, "investor@example.com")

    client.post(f"/startups/{startup_id}/interest", data={"message": "one"})
    client.post(f"/startups/{startup_id}/interest", data={"message": "two"})

    with app.app_context():
        assert InvestorInterest.query.count() == 1


def test_interest_in_pending_startup_is_refused(app, client):
    owner, _ = _owner_and_investor(app)
    startup_id = _startup(app, owner, status="pending")
    make_user(app, email="other@example.com", verified=True, plan="growth")
    login(client, "other@example.com")

    client.post(f"/startups/{startup_id}/interest", data={"message": "hi"})

    with app.app_context():
        assert InvestorInterest.query.count() == 0
